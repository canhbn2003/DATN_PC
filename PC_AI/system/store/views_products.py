"""Product-facing page views."""

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Q, Sum
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone

from .models import OrderItem, Product, ProductImage, Review, UserBehavior
from .views_account import _enforce_active_user, _enforce_password_change
from .views_utils_shared import (
    _apply_product_sorting,
    _apply_search_filters,
    _build_discount_context,
    _build_promotion_context,
    _common_page_context,
    _format_promotion_text,
    _get_product_pricing,
    _get_public_categories_queryset,
    _get_public_products_queryset,
    _get_product_review_summary,
    _load_products_by_ordered_ids,
    _get_session_recommendation_product_ids,
    _get_visible_product_reviews,
    _normalize_product_image_url,
    _parse_description_to_table,
    _pick_primary_image,
    _save_search_history_for_logged_in_user,
    _save_user_behavior_for_logged_in_user,
    _user_has_completed_purchase,
    filter_products_by_price,
)
from .ai.recommenders import item as item_recommender
from .ai.recommenders import user as user_recommender


def _get_hot_sale_products(limit=10):
    """Get products with highest discounts for hot sale section."""
    image_exists_subquery = ProductImage.objects.filter(id_products_id=OuterRef("id_products"))
    products_queryset = (
        _get_public_products_queryset()
        .filter(status="Đang kinh doanh")
        .annotate(has_image=Exists(image_exists_subquery))
    )

    discount_context = _build_discount_context()
    hot_sale_products = []

    for product in products_queryset[:100]:
        pricing = _get_product_pricing(product, discount_context)
        if pricing["has_discount"]:
            product.id = product.id_products
            product.primary_image_url = _pick_primary_image(product)
            product.display_price = f"{pricing['final_price']:,.0f}"
            product.final_price = float(pricing["final_price"])
            product.old_price = f"{pricing['original_price']:,.0f}"
            product.discount_amount_display = f"{pricing['discount_amount']:,.0f}"
            product.has_discount = True
            product.discount_name = pricing["discount_name"]
            product.discount_type = pricing["discount_type"]
            product.discount_percentage = pricing.get("discount_value", 0)
            product.discount_amount = pricing["discount_amount"]
            product.discount_start_date = pricing.get("discount_start_date")

            if pricing["discount_type"] == "percent":
                product.discount_badge = f"Giảm {pricing['discount_value']:,.0f}%"
            else:
                product.discount_badge = f"Giảm {pricing['discount_amount']:,.0f}đ"

            hot_sale_products.append(product)

    hot_sale_products.sort(key=lambda x: (x.discount_amount, x.discount_percentage), reverse=True)
    return hot_sale_products[:limit]


def _format_product_cards(products):
    discount_context = _build_discount_context()
    for product in products:
        pricing = _get_product_pricing(product, discount_context)
        product.primary_image_url = _pick_primary_image(product)
        product.display_price = f"{pricing['final_price']:,.0f}"
        product.old_price = f"{pricing['original_price']:,.0f}"
        product.has_discount = pricing["has_discount"]
        product.discount_name = pricing["discount_name"]
        if pricing["has_discount"]:
            if pricing["discount_type"] == "percent":
                product.discount_badge = f"Giảm {pricing['discount_value']:,.0f}%"
            else:
                product.discount_badge = f"Giảm {pricing['discount_amount']:,.0f}đ"
        else:
            product.discount_badge = ""


def _is_active_product(product):
    try:
        return getattr(product, "status", None) == "Đang kinh doanh"
    except Exception:
        return False


def _filter_out_discontinued(products):
    if not products:
        return []
    return [p for p in products if not getattr(p, "is_discontinued", False) and _is_active_product(p)]


def _get_popular_products_for_recommendation(limit=8):
    products = list(
        _get_public_products_queryset()
        .filter(status="Đang kinh doanh")
        .annotate(total_sold=Coalesce(Sum("orderitem__quantity_order_items"), 0))
        .order_by("-total_sold", "-id_products")[:limit]
    )
    _format_product_cards(products)
    return products


def _get_best_selling_products(limit=24, offset=0, window_days=30, base_queryset=None):
    now = timezone.now()
    cutoff = now - timezone.timedelta(days=int(window_days or 30))
    valid_statuses = ["pending", "confirmed", "shipping", "completed"]

    queryset = base_queryset or _get_public_products_queryset()
    queryset = queryset.annotate(
        recent_sold=Coalesce(
            Sum(
                "orderitem__quantity_order_items",
                filter=Q(
                    orderitem__id_orders__created_at_orders__gte=cutoff,
                    orderitem__id_orders__status_orders__in=valid_statuses,
                ),
            ),
            0,
        ),
        total_sold=Coalesce(
            Sum(
                "orderitem__quantity_order_items",
                filter=Q(orderitem__id_orders__status_orders__in=valid_statuses),
            ),
            0,
        ),
    ).order_by("-recent_sold", "-total_sold", "-id_products")

    safe_offset = max(0, int(offset or 0))
    safe_limit = max(1, int(limit or 24))
    products = list(queryset[safe_offset:safe_offset + safe_limit])
    _format_product_cards(products)
    return products


def _pick_recommendation_display_count(total_count, limit):
    if total_count <= 0:
        return 0
    if limit >= 12:
        if total_count >= 12:
            return 12
        if total_count >= 6:
            return 6
    return min(total_count, limit)


def _fill_recommendations_to_limit(initial_products, limit=12, exclude_ids=None):
    if not isinstance(limit, int) or limit <= 0:
        return initial_products

    products = [p for p in (initial_products or []) if not getattr(p, "is_discontinued", False) and getattr(p, "status", "") == "Đang kinh doanh"]
    exclude = set(exclude_ids or [])
    existing_ids = {getattr(p, "id_products", None) for p in products if getattr(p, "id_products", None) is not None}
    exclude.update(existing_ids)

    if len(products) >= limit:
        return products[:limit]

    popular_candidates = _get_popular_products_for_recommendation(limit=limit * 3)
    for p in popular_candidates:
        if getattr(p, "id_products", None) in exclude:
            continue
        products.append(p)
        exclude.add(p.id_products)
        if len(products) >= limit:
            break

    if len(products) < limit:
        more = list(_get_public_products_queryset().filter(status="Đang kinh doanh").order_by("-id_products")[: limit * 3])
        for p in more:
            if getattr(p, "id_products", None) in exclude:
                continue
            products.append(p)
            exclude.add(p.id_products)
            if len(products) >= limit:
                break

    return products[:limit]


def _get_personalized_products_for_home(request, limit=8):
    user_id = request.session.get("logged_in_user_id")
    session_product_ids = _get_session_recommendation_product_ids(request, limit=max(limit * 2, 12))

    if not user_id:
        if session_product_ids:
            session_products = _load_products_by_ordered_ids(session_product_ids)
            _format_product_cards(session_products)
            active_session_products = [p for p in session_products if not getattr(p, "is_discontinued", False) and getattr(p, "status", "") == "Đang kinh doanh"]
            filled = _fill_recommendations_to_limit(active_session_products, limit=limit)
            return filled, "session"

        popular_products = _get_popular_products_for_recommendation(limit=limit)
        filled = _fill_recommendations_to_limit(popular_products, limit=limit)
        return filled, "popular"

    ranking_fetch_limit = max(limit * 3, 18)
    user_based_rows = []
    item_based_rows = []

    try:
        user_based_rows = user_recommender.recommend_for_user(int(user_id), top_n=ranking_fetch_limit)
    except Exception:
        user_based_rows = []

    try:
        item_based_rows = item_recommender.recommend_for_user(int(user_id), top_n=ranking_fetch_limit)
    except Exception:
        item_based_rows = []

    user_scores = {}
    item_scores = {}
    for row in user_based_rows:
        product_id = row.get("id_products")
        score = row.get("score")
        if not product_id:
            continue
        user_scores[int(product_id)] = float(score or 0.0)

    for row in item_based_rows:
        product_id = row.get("id_products")
        score = row.get("score")
        if not product_id:
            continue
        item_scores[int(product_id)] = float(score or 0.0)

    session_scores = {}
    total_session_ids = len(session_product_ids)
    if total_session_ids:
        for index, product_id in enumerate(session_product_ids):
            session_scores[int(product_id)] = float(total_session_ids - index) / float(total_session_ids)

    if not session_scores and not user_scores and not item_scores:
        popular_products = _get_popular_products_for_recommendation(limit=limit)
        filled = _fill_recommendations_to_limit(popular_products, limit=limit)
        return filled, "popular"

    def _normalize_scores(score_map):
        max_score = max(score_map.values(), default=0.0)
        if max_score <= 0:
            return {}
        return {int(product_id): float(score) / max_score for product_id, score in score_map.items()}

    normalized_session_scores = _normalize_scores(session_scores)
    normalized_user_scores = _normalize_scores(user_scores)
    normalized_item_scores = _normalize_scores(item_scores)

    active_components = []
    active_sources = []
    if normalized_session_scores:
        active_components.append(("session", normalized_session_scores, 0.30))
        active_sources.append("session")
    if normalized_user_scores:
        active_components.append(("user", normalized_user_scores, 0.40))
        active_sources.append("user")
    if normalized_item_scores:
        active_components.append(("item", normalized_item_scores, 0.30))
        active_sources.append("item")

    if not active_components:
        popular_products = _get_popular_products_for_recommendation(limit=limit)
        display_count = _pick_recommendation_display_count(len(popular_products), limit)
        return popular_products[:display_count], "popular"

    total_weight = sum(weight for _, _, weight in active_components)
    normalized_components = [(name, score_map, weight / total_weight) for name, score_map, weight in active_components]

    combined_scores = {}
    all_candidate_ids = set()
    for _, score_map, _ in normalized_components:
        all_candidate_ids.update(score_map.keys())

    for product_id in all_candidate_ids:
        blended = 0.0
        for _, score_map, component_weight in normalized_components:
            blended += component_weight * score_map.get(product_id, 0.0)
        combined_scores[product_id] = blended

    recommendation_source = "+".join(active_sources)

    recommended_ids = [
        product_id
        for product_id, _ in sorted(combined_scores.items(), key=lambda pair: (pair[1], pair[0]), reverse=True)
    ]

    if not recommended_ids:
        if session_product_ids:
            session_products = _load_products_by_ordered_ids(session_product_ids)
            _format_product_cards(session_products)
            active_session_products = [p for p in session_products if not getattr(p, "is_discontinued", False) and getattr(p, "status", "") == "Đang kinh doanh"]
            filled = _fill_recommendations_to_limit(active_session_products, limit=limit)
            return filled, "session"

        popular_products = _get_popular_products_for_recommendation(limit=limit)
        filled = _fill_recommendations_to_limit(popular_products, limit=limit)
        return filled, "popular"

    ordered_products = _load_products_by_ordered_ids(recommended_ids)
    _format_product_cards(ordered_products)
    active_ordered = [p for p in ordered_products if not getattr(p, "is_discontinued", False) and getattr(p, "status", "") == "Đang kinh doanh"]
    filled = _fill_recommendations_to_limit(active_ordered, limit=limit)
    return filled, recommendation_source


def home_page(request):
    selected_category_id = (request.GET.get("category") or "").strip()
    search_query = (request.GET.get("q") or "").strip()
    layout_mode = (request.GET.get("layout") or "").strip().lower()
    is_all_products_page = layout_mode == "all-products"
    is_search_result = bool(search_query)
    selected_brand = (request.GET.get("brand") or "").strip()
    selected_price_range = (request.GET.get("price_range") or "").strip()
    selected_sort = (request.GET.get("sort") or "best_selling").strip().lower() or "best_selling"
    selected_best_selling = (request.GET.get("best_selling") or "").strip().lower() in {"1", "true", "on", "yes"}
    has_product_filters = bool(selected_category_id or search_query or selected_brand or selected_price_range or selected_best_selling)

    image_exists_subquery = ProductImage.objects.filter(id_products_id=OuterRef("id_products"))
    products_queryset = _get_public_products_queryset().annotate(has_image=Exists(image_exists_subquery))

    selected_category_name = ""
    if selected_category_id.isdigit():
        products_queryset = products_queryset.filter(id_categories_id=int(selected_category_id))
        selected_category = _get_public_categories_queryset().filter(id_categories=int(selected_category_id)).first()
        if selected_category:
            selected_category_name = selected_category.name_categories

    products_queryset = filter_products_by_price(request, base_queryset=products_queryset)
    products_queryset = _apply_search_filters(products_queryset, search_query, selected_brand, selected_price_range)

    _save_search_history_for_logged_in_user(request, search_query)

    products_queryset = products_queryset.annotate(total_sold=Coalesce(Sum("orderitem__quantity_order_items"), 0))
    if selected_best_selling:
        products_queryset = products_queryset.filter(total_sold__gt=0)
    products_queryset = _apply_product_sorting(products_queryset, selected_sort)
    paginator = Paginator(products_queryset, 18)
    page_obj = paginator.get_page(request.GET.get("page") or 1)
    products = list(page_obj)
    discount_context = _build_discount_context()

    for product in products:
        pricing = _get_product_pricing(product, discount_context)
        product.primary_image_url = _pick_primary_image(product)
        product.display_price = f"{pricing['final_price']:,.0f}"
        product.old_price = f"{pricing['original_price']:,.0f}"
        product.discount_amount_display = f"{pricing['discount_amount']:,.0f}"
        product.has_discount = pricing["has_discount"]
        product.discount_name = pricing["discount_name"]
        if pricing["has_discount"]:
            if pricing["discount_type"] == "percent":
                product.discount_badge = f"Giảm {pricing['discount_value']:,.0f}%"
            else:
                product.discount_badge = f"Giảm {pricing['discount_amount']:,.0f}đ"
        else:
            product.discount_badge = ""

    context = _common_page_context(request)
    context["products"] = products
    context["page_obj"] = page_obj
    context["paginator"] = paginator
    query_params = request.GET.copy()
    if "page" in query_params:
        query_params.pop("page")
    context["base_query"] = query_params.urlencode()
    context["selected_category_id"] = selected_category_id
    context["selected_category_name"] = selected_category_name
    context["search_query"] = search_query
    context["is_search_result"] = is_search_result
    context["has_product_filters"] = has_product_filters
    context["selected_brand"] = selected_brand
    context["selected_price_range"] = selected_price_range
    context["selected_sort"] = selected_sort
    context["selected_best_selling"] = selected_best_selling
    context["is_all_products_page"] = is_all_products_page
    context["catalog_title"] = "Tất cả sản phẩm" if is_all_products_page else "SẢN PHẨM BÁN CHẠY"
    context["hide_home_hero"] = is_all_products_page
    context["hide_home_promotions"] = is_all_products_page

    if not is_all_products_page:
        context["hot_sale_products"] = _get_hot_sale_products(limit=10)
        recommended_products, recommendation_source = _get_personalized_products_for_home(request, limit=12)
        context["recommended_products"] = recommended_products
        context["recommendation_source"] = recommendation_source
    else:
        context["hot_sale_products"] = []
        context["recommended_products"] = []
        context["recommendation_source"] = ""

    return render(request, "store/pages/home.html", context)


def all_products_page(request):
    return redirect("/?layout=all-products")


def product_detail_page(request, product_id):
    user_id = request.session.get("logged_in_user_id")

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()
        if action == "submit_review":
            if not user_id:
                messages.error(request, "Vui lòng đăng nhập để đánh giá sản phẩm.")
                return redirect(f"/products/{product_id}/?auth=login")

            locked_response = _enforce_active_user(request)
            if locked_response:
                return locked_response

            password_response = _enforce_password_change(request)
            if password_response:
                return password_response

            if not _user_has_completed_purchase(user_id, product_id):
                messages.error(request, "Bạn chỉ có thể đánh giá sau khi đã mua và nhận sản phẩm (đơn hoàn thành).")
                return redirect("product_detail_page", product_id=product_id)

            try:
                rating = int(request.POST.get("rating") or 0)
            except (TypeError, ValueError):
                rating = 0

            if rating < 1 or rating > 5:
                messages.error(request, "Vui lòng chọn số sao từ 1 đến 5.")
                return redirect("product_detail_page", product_id=product_id)

            comment = (request.POST.get("comment") or "").strip()
            if len(comment) > 2000:
                messages.error(request, "Nội dung đánh giá quá dài (tối đa 2000 ký tự).")
                return redirect("product_detail_page", product_id=product_id)

            existing_review = Review.objects.filter(id_users_id=user_id, id_products_id=product_id).order_by("-id_reviews").first()
            if existing_review:
                existing_review.rating = rating
                existing_review.comment = comment
                existing_review.status = "Hiển thị"
                existing_review.save(update_fields=["rating", "comment", "status"])
                messages.success(request, "Đã cập nhật đánh giá của bạn. Cảm ơn bạn!")
            else:
                Review.objects.create(
                    id_users_id=user_id,
                    id_products_id=product_id,
                    rating=rating,
                    comment=comment,
                    status="Hiển thị",
                )
                messages.success(request, "Cảm ơn bạn đã đánh giá sản phẩm!")

            return redirect("product_detail_page", product_id=product_id)

    try:
        product_obj = _get_public_products_queryset().get(id_products=product_id)
    except Product.DoesNotExist as exc:
        raise Http404("Product not found") from exc

    _save_user_behavior_for_logged_in_user(request, product_id, "view")

    gallery_images = [
        _normalize_product_image_url(item.image_url)
        for item in product_obj.images.all()
        if item.image_url and _normalize_product_image_url(item.image_url)
    ]

    fallback_image = _pick_primary_image(product_obj)
    if not gallery_images and fallback_image:
        gallery_images = [fallback_image]

    product_specs = []

    sold_count = (
        OrderItem.objects.filter(id_products_id=product_obj.id_products)
        .exclude(id_orders__status_orders="cancelled")
        .aggregate(total_sold=Coalesce(Sum("quantity_order_items"), 0))
        .get("total_sold", 0)
    )

    review_summary = _get_product_review_summary(product_obj.id_products)
    visible_reviews = _get_visible_product_reviews(product_obj.id_products, limit=25)

    can_review = False
    my_review = None
    if user_id:
        can_review = _user_has_completed_purchase(user_id, product_obj.id_products)
        my_review = Review.objects.filter(id_users_id=user_id, id_products_id=product_obj.id_products).order_by("-id_reviews").first()

    pricing = _get_product_pricing(product_obj)

    stock = product_obj.stock or 0
    stock_status = ""
    is_out_of_stock = stock == 0
    is_low_stock = stock > 0 and stock < 5
    if is_out_of_stock:
        stock_status = "Hết hàng"
    elif is_low_stock:
        stock_status = "Sắp hết hàng"

    product = {
        "id": product_obj.id_products,
        "name": product_obj.name_products,
        "price": f"{pricing['final_price']:,.0f}",
        "old_price": f"{pricing['original_price']:,.0f}",
        "discount_amount": f"{pricing['discount_amount']:,.0f}",
        "has_discount": pricing["has_discount"],
        "discount_name": pricing["discount_name"],
        "description": product_obj.description or "Sản phẩm chính hãng, bảo hành đầy đủ và hỗ trợ kỹ thuật tận nơi.",
        "brand": product_obj.brand or "N/A",
        "brand_slug": (product_obj.brand or "").strip(),
        "category": product_obj.id_categories.name_categories if product_obj.id_categories else "Khác",
        "category_id": product_obj.id_categories_id if product_obj.id_categories else None,
        "primary_image": gallery_images[0] if gallery_images else "",
        "gallery": gallery_images,
        "sold_count": sold_count,
        "rating_value": review_summary["avg_rating"],
        "rating_count": review_summary["total_reviews"],
        "rating_star_count": review_summary["star_count"],
        "status": product_obj.status,
        "is_discontinued": product_obj.is_discontinued,
        "stock": stock,
        "stock_status": stock_status,
        "is_out_of_stock": is_out_of_stock,
        "is_low_stock": is_low_stock,
    }

    context = _common_page_context(request)
    promotion_context = _build_promotion_context()
    recommended_products, recommendation_source = _get_personalized_products_for_home(request, limit=6)
    recommended_products = [item for item in recommended_products if int(getattr(item, "id_products", 0) or 0) != int(product_id)][:6]

    description_table = _parse_description_to_table(product_obj.description)

    context.update(
        {
            "product": product,
            "product_specs": product_specs,
            "description_table": description_table,
            "recommended_products": recommended_products,
            "recommendation_source": recommendation_source,
            "reviews": visible_reviews,
            "can_review": can_review,
            "my_review": my_review,
            "promotions": [_format_promotion_text(item) for item in promotion_context["promotions_by_code"].values()],
        }
    )
    return render(request, "store/pages/product_detail.html", context)


def viewed_products_page(request):
    user_id = request.session.get("logged_in_user_id")
    if not user_id:
        messages.error(request, "Vui lòng đăng nhập để xem sản phẩm đã xem")
        return redirect("/?auth=login")

    locked_response = _enforce_active_user(request)
    if locked_response:
        return locked_response

    password_response = _enforce_password_change(request)
    if password_response:
        return password_response

    behavior_rows = (
        UserBehavior.objects.filter(id_users_id=user_id, action_type_user_behavior="view")
        .order_by("-created_at_user_behavior")
        .values_list("id_products_id", flat=True)
    )

    product_ids = []
    for product_id in behavior_rows:
        if product_id not in product_ids:
            product_ids.append(product_id)
        if len(product_ids) >= 60:
            break

    products = []
    if product_ids:
        products_map = {item.id_products: item for item in _get_public_products_queryset().filter(id_products__in=product_ids)}
        products = [products_map[product_id] for product_id in product_ids if product_id in products_map]

    discount_context = _build_discount_context()
    for product in products:
        pricing = _get_product_pricing(product, discount_context)
        product.primary_image_url = _pick_primary_image(product)
        product.display_price = f"{pricing['final_price']:,.0f}"
        product.old_price = f"{pricing['original_price']:,.0f}"
        product.discount_amount_display = f"{pricing['discount_amount']:,.0f}"
        product.has_discount = pricing["has_discount"]
        product.discount_name = pricing["discount_name"]
        if pricing["has_discount"]:
            if pricing["discount_type"] == "percent":
                product.discount_badge = f"Giảm {pricing['discount_value']:,.0f}%"
            else:
                product.discount_badge = f"Giảm {pricing['discount_amount']:,.0f}đ"
        else:
            product.discount_badge = ""

    context = _common_page_context(request)
    context.update({"products": products})
    return render(request, "store/pages/viewed_products.html", context)


def purchased_products_page(request):
    user_id = request.session.get("logged_in_user_id")
    if not user_id:
        messages.error(request, "Vui lòng đăng nhập để xem sản phẩm đã mua")
        return redirect("/?auth=login")

    locked_response = _enforce_active_user(request)
    if locked_response:
        return locked_response

    password_response = _enforce_password_change(request)
    if password_response:
        return password_response

    purchased_rows = (
        OrderItem.objects.filter(id_orders__id_users_id=user_id)
        .exclude(id_orders__status_orders="cancelled")
        .order_by("-id_orders__created_at_orders", "-id_order_items")
        .values_list("id_products_id", flat=True)
    )

    ordered_product_ids = []
    for product_id in purchased_rows:
        if product_id not in ordered_product_ids:
            ordered_product_ids.append(product_id)

    products = []
    if ordered_product_ids:
        products_map = {item.id_products: item for item in _get_public_products_queryset().filter(id_products__in=ordered_product_ids)}
        products = [products_map[product_id] for product_id in ordered_product_ids if product_id in products_map]

    completed_product_ids = set(
        OrderItem.objects.filter(
            id_orders__id_users_id=user_id,
            id_orders__status_orders="completed",
            id_products_id__in=ordered_product_ids,
        ).values_list("id_products_id", flat=True)
    )
    reviewed_product_ids = set(
        Review.objects.filter(id_users_id=user_id, id_products_id__in=ordered_product_ids).values_list("id_products_id", flat=True)
    )

    discount_context = _build_discount_context()
    for product in products:
        pricing = _get_product_pricing(product, discount_context)
        product.primary_image_url = _pick_primary_image(product)
        product.display_price = f"{pricing['final_price']:,.0f}"
        product.old_price = f"{pricing['original_price']:,.0f}"
        product.discount_amount_display = f"{pricing['discount_amount']:,.0f}"
        product.has_discount = pricing["has_discount"]
        product.discount_name = pricing["discount_name"]
        if pricing["has_discount"]:
            if pricing["discount_type"] == "percent":
                product.discount_badge = f"Giảm {pricing['discount_value']:,.0f}%"
            else:
                product.discount_badge = f"Giảm {pricing['discount_amount']:,.0f}đ"
        else:
            product.discount_badge = ""
        product.can_review = product.id_products in completed_product_ids
        product.has_reviewed = product.id_products in reviewed_product_ids

    category_group_map = {}
    for product in products:
        category_id = product.id_categories_id or 0
        if category_id not in category_group_map:
            category_group_map[category_id] = {
                "category_id": category_id,
                "category_name": product.id_categories.name_categories if product.id_categories else "Khác",
                "products": [],
            }
        category_group_map[category_id]["products"].append(product)

    grouped_categories = sorted(category_group_map.values(), key=lambda item: (item["category_name"] or "").lower())

    context = _common_page_context(request)
    context.update(
        {
            "grouped_categories": grouped_categories,
            "purchased_product_count": len(products),
            "pending_review_count": sum(1 for item in products if getattr(item, "can_review", False) and not getattr(item, "has_reviewed", False)),
        }
    )

    return render(request, "store/pages/purchased_products.html", context)


__all__ = [
    "home_page",
    "all_products_page",
    "product_detail_page",
    "viewed_products_page",
    "purchased_products_page",
]
