import json
from decimal import Decimal
from urllib.parse import urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q, Sum
from django.db.models.functions import Coalesce
from django.http import JsonResponse, Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from .models import (
    Cart,
    CartItem,
    Category,
    Discount,
    DiscountCategory,
    DiscountProduct,
    Order,
    OrderItem,
    Product,
    ProductImage,
    Promotion,
    PromotionProduct,
    SearchHistory,
    User,
    UserBehavior,
)
from django.views.decorators.csrf import csrf_exempt


OLD_PRICE_MULTIPLIER = Decimal("1.15")


SAVED_PROMOTION_CODES_SESSION_KEY = "saved_promotion_codes"


PRICE_RANGES = (
    ("duoi-5", "Duoi 5 trieu", Decimal("0"), Decimal("5000000")),
    ("5-10", "Tu 5 - 10 trieu", Decimal("5000000"), Decimal("10000000")),
    ("10-20", "Tu 10 - 20 trieu", Decimal("10000000"), Decimal("20000000")),
    ("tren-20", "Tren 20 trieu", Decimal("20000000"), None),
)


IMAGE_BLOCKED_KEYWORDS = (
    "/template/",
    "/media/category/",
    "/category/",
    "feedback",
    "icon",
    "logo",
    "banner",
    "zalo",
    "facebook",
    "youtube",
)


def _is_valid_product_image_url(url):
    if not url:
        return False

    path = urlparse(url).path.lower()
    if not path:
        return False

    if any(keyword in path for keyword in IMAGE_BLOCKED_KEYWORDS):
        return False

    return any(ext in path for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"))


def _normalize_product_image_url(url):
    if not url:
        return ""

    raw_url = str(url).strip()
    if not raw_url:
        return ""

    # Keep absolute URLs and root-relative paths as-is.
    parsed = urlparse(raw_url)
    if parsed.scheme in ("http", "https") or raw_url.startswith("//") or raw_url.startswith("/"):
        return raw_url

    media_prefix = (settings.MEDIA_URL or "/media/").rstrip("/") + "/"
    normalized_path = raw_url.replace("\\", "/").lstrip("/")
    media_prefix_no_slash = media_prefix.lstrip("/")

    if normalized_path.lower().startswith(media_prefix_no_slash.lower()):
        return f"/{normalized_path}"

    return f"{media_prefix}{normalized_path}"


def _pick_primary_image(product):
    # Lấy danh sách ảnh đã prefetch hoặc truy vấn trực tiếp
    images = getattr(product, "images", None)
    if images is not None:
        image_list = list(images.all())
    else:
        image_list = list(getattr(product, "productimage_set", []).all()) if hasattr(product, "productimage_set") else []

    # Ưu tiên ảnh chính (is_main=True)
    for item in image_list:
        if getattr(item, "is_main", False) and _is_valid_product_image_url(item.image_url):
            return _normalize_product_image_url(item.image_url)

    # Nếu không có ảnh chính, lấy ảnh đầu tiên hợp lệ
    for item in image_list:
        if _is_valid_product_image_url(item.image_url):
            return _normalize_product_image_url(item.image_url)

    return None


def _calculate_reduction_amount(base_amount, discount_type, discount_value, max_discount=None):
    if base_amount <= 0:
        return Decimal("0")

    reduction = Decimal("0")
    value = Decimal(discount_value or 0)

    if discount_type == "percent":
        reduction = (base_amount * value) / Decimal("100")
    elif discount_type == "fixed":
        reduction = value

    if max_discount is not None:
        reduction = min(reduction, Decimal(max_discount))

    reduction = max(Decimal("0"), reduction)
    reduction = min(reduction, base_amount)
    return reduction


def _build_discount_context():
    now = timezone.now()
    active_discounts = list(
        Discount.objects.filter(
            status=True,
            start_date__lte=now,
            end_date__gte=now,
        )
    )

    if not active_discounts:
        return {
            "discounts_by_id": {},
            "ordered_discount_ids": [],
            "all_discount_ids": set(),
            "product_discount_map": {},
            "category_discount_map": {},
        }

    discounts_by_id = {item.id_discounts: item for item in active_discounts}
    ordered_discount_ids = [
        item.id_discounts
        for item in sorted(
            active_discounts,
            key=lambda d: (d.start_date, d.id_discounts),
            reverse=True,
        )
    ]
    all_discount_ids = {item.id_discounts for item in active_discounts if item.apply_type == "all"}
    product_scope_discount_ids = {item.id_discounts for item in active_discounts if item.apply_type == "product"}
    category_scope_discount_ids = {item.id_discounts for item in active_discounts if item.apply_type == "category"}

    product_discount_map = {}
    product_rows = DiscountProduct.objects.filter(id_discounts_id__in=product_scope_discount_ids).values_list(
        "id_products_id",
        "id_discounts_id",
    )
    for product_id, discount_id in product_rows:
        product_discount_map.setdefault(product_id, set()).add(discount_id)

    category_discount_map = {}
    category_rows = DiscountCategory.objects.filter(id_discounts_id__in=category_scope_discount_ids).values_list(
        "id_categories_id",
        "id_discounts_id",
    )
    for category_id, discount_id in category_rows:
        category_discount_map.setdefault(category_id, set()).add(discount_id)

    return {
        "discounts_by_id": discounts_by_id,
        "ordered_discount_ids": ordered_discount_ids,
        "all_discount_ids": all_discount_ids,
        "product_discount_map": product_discount_map,
        "category_discount_map": category_discount_map,
    }


def _get_product_pricing(product, discount_context=None):
    discount_context = discount_context or _build_discount_context()
    discounts_by_id = discount_context["discounts_by_id"]
    ordered_discount_ids = discount_context.get("ordered_discount_ids", [])

    original_price = Decimal(product.price or 0)
    product_discount_ids = set(discount_context["product_discount_map"].get(product.id_products, set()))

    category_discount_ids = set()
    category_id = getattr(product, "id_categories_id", None)
    if category_id is not None:
        category_discount_ids = set(discount_context["category_discount_map"].get(category_id, set()))

    all_discount_ids = set(discount_context["all_discount_ids"])

    def _pick_scoped_discount(discount_ids):
        for discount_id in ordered_discount_ids:
            if discount_id not in discount_ids:
                continue

            discount = discounts_by_id.get(discount_id)
            if not discount:
                continue

            reduction = _calculate_reduction_amount(
                original_price,
                discount.discount_type,
                discount.discount_value,
            )
            if reduction > 0:
                return discount, reduction

        return None, Decimal("0")

    best_discount = None
    best_reduction = Decimal("0")

    # Priority order: product-level -> category-level -> global/all-level.
    # Within each scope, choose the newest active discount (by start_date then id).
    for scoped_ids in (product_discount_ids, category_discount_ids, all_discount_ids):
        candidate_discount, candidate_reduction = _pick_scoped_discount(scoped_ids)
        if candidate_discount and candidate_reduction > 0:
            best_discount = candidate_discount
            best_reduction = candidate_reduction
            break

    final_price = max(Decimal("0"), original_price - best_reduction)

    return {
        "original_price": original_price,
        "discount_amount": best_reduction,
        "final_price": final_price,
        "has_discount": best_reduction > 0,
        "discount_name": best_discount.name if best_discount else "",
        "discount_type": best_discount.discount_type if best_discount else "",
        "discount_value": best_discount.discount_value if best_discount else Decimal("0"),
    }


def _build_promotion_context():
    now = timezone.now()
    promotions = list(
        Promotion.objects.filter(
            status=True,
            start_date__lte=now,
            end_date__gte=now,
            used_count__lt=F("usage_limit"),
        )
    )

    if not promotions:
        return {
            "promotions_by_code": {},
            "promotion_product_map": {},
        }

    promotion_ids = [item.id_promotions for item in promotions]
    promotions_by_code = {item.code.upper(): item for item in promotions}

    promotion_product_map = {}
    rows = PromotionProduct.objects.filter(id_promotions_id__in=promotion_ids).values_list(
        "id_promotions_id",
        "id_products_id",
    )
    for promotion_id, product_id in rows:
        promotion_product_map.setdefault(promotion_id, set()).add(product_id)

    return {
        "promotions_by_code": promotions_by_code,
        "promotion_product_map": promotion_product_map,
    }


def _evaluate_promotion_code(promotion_code, cart_lines, promotion_context=None):
    code = (promotion_code or "").strip().upper()
    if not code:
        return {
            "is_applied": False,
            "code": "",
            "amount": Decimal("0"),
            "message": "",
            "reason": "empty",
        }

    promotion_context = promotion_context or _build_promotion_context()
    promotion = promotion_context["promotions_by_code"].get(code)
    if not promotion:
        return {
            "is_applied": False,
            "code": code,
            "amount": Decimal("0"),
            "message": f"Mã {code} không hợp lệ hoặc đã hết hạn.",
            "reason": "invalid",
        }

    eligible_product_ids = promotion_context["promotion_product_map"].get(promotion.id_promotions, set())
    matching_lines = []
    if eligible_product_ids:
        matching_lines = [line for line in cart_lines if line["product_id"] in eligible_product_ids]
        if not matching_lines:
            return {
                "is_applied": False,
                "code": code,
                "amount": Decimal("0"),
                "message": f"Mã {code} không áp dụng cho sản phẩm trong giỏ hàng.",
                "reason": "product_scope_mismatch",
            }

    if eligible_product_ids:
        eligible_subtotal = sum(
            (line["line_total_after_product_discount"] for line in matching_lines),
            Decimal("0"),
        )
    else:
        eligible_subtotal = sum((line["line_total_after_product_discount"] for line in cart_lines), Decimal("0"))

    min_order_value = Decimal(promotion.min_order_value or 0)
    if eligible_subtotal < min_order_value:
        return {
            "is_applied": False,
            "code": code,
            "amount": Decimal("0"),
            "message": f"Đơn tối thiểu {min_order_value:,.0f} đ để dùng mã {code}.",
            "reason": "min_order_not_met",
        }

    reduction = _calculate_reduction_amount(
        eligible_subtotal,
        promotion.discount_type,
        promotion.discount_value,
        promotion.max_discount,
    )

    return {
        "is_applied": reduction > 0,
        "code": code,
        "amount": reduction,
        "message": f"Đã áp dụng mã {code}." if reduction > 0 else "Mã giảm giá không áp dụng được cho đơn này.",
        "name": code,
        "reason": "applied" if reduction > 0 else "not_applicable",
    }


def _format_promotion_text(promotion):
    if promotion.discount_type == "percent":
        discount_part = f"Giảm {promotion.discount_value:,.0f}%"
    else:
        discount_part = f"Giảm {promotion.discount_value:,.0f} đ"

    cap_part = ""
    if promotion.max_discount:
        cap_part = f", tối đa {promotion.max_discount:,.0f} đ"

    min_part = ""
    if promotion.min_order_value:
        min_part = f", đơn từ {promotion.min_order_value:,.0f} đ"

    return f"Mã {promotion.code}: {discount_part}{cap_part}{min_part}"


def _get_saved_promotion_codes(request):
    raw_codes = request.session.get(SAVED_PROMOTION_CODES_SESSION_KEY, [])
    if not isinstance(raw_codes, (list, tuple)):
        raw_codes = []

    cleaned_codes = []
    for code in raw_codes:
        normalized_code = (str(code) or "").strip().upper()
        if normalized_code and normalized_code not in cleaned_codes:
            cleaned_codes.append(normalized_code)

    if cleaned_codes != list(raw_codes):
        request.session[SAVED_PROMOTION_CODES_SESSION_KEY] = cleaned_codes
        request.session.modified = True

    return cleaned_codes


def _set_saved_promotion_codes(request, codes):
    cleaned_codes = []
    for code in codes:
        normalized_code = (str(code) or "").strip().upper()
        if normalized_code and normalized_code not in cleaned_codes:
            cleaned_codes.append(normalized_code)

    request.session[SAVED_PROMOTION_CODES_SESSION_KEY] = cleaned_codes
    request.session.modified = True
    return cleaned_codes


def _build_promotion_card_data(promotion, promotion_context, saved_codes=None, cart_lines=None):
    saved_codes = set(saved_codes or [])
    eligible_product_ids = promotion_context["promotion_product_map"].get(promotion.id_promotions, set())
    scope_text = "Áp dụng cho toàn bộ đơn hàng"
    if eligible_product_ids:
        scope_text = f"Áp dụng cho {len(eligible_product_ids)} sản phẩm"

    usage_left = max(int(promotion.usage_limit or 0) - int(promotion.used_count or 0), 0)
    card_data = {
        "code": promotion.code,
        "text": _format_promotion_text(promotion),
        "scope_text": scope_text,
        "start_date": promotion.start_date,
        "end_date": promotion.end_date,
        "usage_left": usage_left,
        "is_saved": promotion.code.upper() in saved_codes,
        "save_button_label": "Bỏ lưu" if promotion.code.upper() in saved_codes else "Lưu mã",
        "status_label": "Đã lưu" if promotion.code.upper() in saved_codes else "Chưa lưu",
    }

    if cart_lines is not None:
        preview = _evaluate_promotion_code(promotion.code, cart_lines, promotion_context)
        card_data.update(
            {
                "is_eligible": preview["is_applied"],
                "preview_discount": f"{preview['amount']:,.0f}",
                "message": preview.get("message", ""),
            }
        )

    return card_data


@require_http_methods(["GET", "POST"])
def flash_sale_page(request):
    promotion_context = _build_promotion_context()
    saved_codes = _get_saved_promotion_codes(request)

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()
        code = (request.POST.get("code") or "").strip().upper()

        if not code:
            messages.error(request, "Vui lòng chọn mã giảm giá")
            return redirect("flash_sale_page")

        promotion = promotion_context["promotions_by_code"].get(code)
        if not promotion:
            messages.error(request, f"Mã {code} không hợp lệ hoặc đã hết hạn")
            return redirect("flash_sale_page")

        if action == "save":
            if code not in saved_codes:
                saved_codes.insert(0, code)
                _set_saved_promotion_codes(request, saved_codes)
            messages.success(request, f"Đã lưu mã {code}")
        elif action == "remove":
            if code in saved_codes:
                saved_codes = [item for item in saved_codes if item != code]
                _set_saved_promotion_codes(request, saved_codes)
            messages.success(request, f"Đã bỏ lưu mã {code}")
        else:
            messages.error(request, "Hành động không hợp lệ")

        return redirect("flash_sale_page")

    promotions = [
        _build_promotion_card_data(promotion, promotion_context, saved_codes=saved_codes)
        for promotion in sorted(
            promotion_context["promotions_by_code"].values(),
            key=lambda item: (item.end_date, item.id_promotions),
        )
    ]

    context = _common_page_context(request)
    context.update(
        {
            "promotions": promotions,
            "saved_promotion_codes": saved_codes,
            "saved_promotion_count": len(saved_codes),
        }
    )
    return render(request, "store/pages/flash_sale.html", context)


def _common_page_context(request):
    categories = list(Category.objects.all().order_by("name_categories")[:10])
    category_ids = [item.id_categories for item in categories]

    brand_map = {category_id: [] for category_id in category_ids}
    if category_ids:
        brand_rows = (
            Product.objects.filter(id_categories_id__in=category_ids)
            .exclude(Q(brand__isnull=True) | Q(brand__exact=""))
            .values_list("id_categories_id", "brand")
            .distinct()
            .order_by("brand")
        )

        for category_id, brand_name in brand_rows:
            brand_value = (brand_name or "").strip()
            if not brand_value:
                continue

            existing_brands = brand_map.get(category_id, [])
            if len(existing_brands) < 8:
                existing_brands.append(brand_value)

    for item in categories:
        item.brand_list = brand_map.get(item.id_categories, [])

    selected_brand = (request.GET.get("brand") or "").strip()
    selected_price_range = (request.GET.get("price_range") or "").strip()

    price_ranges = [
        {
            "code": code,
            "label": label,
        }
        for code, label, _, _ in PRICE_RANGES
    ]

    auth_tab = (request.GET.get("auth") or request.GET.get("auth_tab") or "").strip()

    return {
        "categories": categories,
        "auth_tab": auth_tab,
        "selected_category_id": (request.GET.get("category") or "").strip(),
        "search_query": (request.GET.get("q") or "").strip(),
        "selected_brand": selected_brand,
        "selected_price_range": selected_price_range,
        "price_ranges": price_ranges,
        "logged_in_user_name": request.session.get("logged_in_user_name"),
        "register_errors": request.session.pop("register_errors", {}),
        "register_old": request.session.pop("register_old", {}),
    }


def _apply_search_filters(products_queryset, keyword, brand_filter="", price_range_code=""):
    if not keyword:
        filtered_queryset = products_queryset
    else:
        # Search by character fragments/tokens so users do not need exact full names.
        keyword_value = keyword.strip()
        keyword_tokens = [token for token in keyword_value.split() if token]

        filtered_queryset = products_queryset.filter(
            Q(name_products__icontains=keyword_value)
            | Q(brand__icontains=keyword_value)
            | Q(description__icontains=keyword_value)
        )

        for token in keyword_tokens:
            filtered_queryset = filtered_queryset.filter(
                Q(name_products__icontains=token)
                | Q(brand__icontains=token)
                | Q(description__icontains=token)
            )

    if brand_filter:
        filtered_queryset = filtered_queryset.filter(brand__iexact=brand_filter)

    selected_range = next((item for item in PRICE_RANGES if item[0] == price_range_code), None)
    if selected_range:
        _, _, min_price, max_price = selected_range
        filtered_queryset = filtered_queryset.filter(price__gte=min_price)
        if max_price is not None:
            filtered_queryset = filtered_queryset.filter(price__lt=max_price)

    return filtered_queryset


def _save_search_history_for_logged_in_user(request, keyword):
    keyword_value = (keyword or "").strip()
    if not keyword_value:
        return

    user_id = request.session.get("logged_in_user_id")
    if not user_id:
        return

    SearchHistory.objects.create(
        id_users_id=user_id,
        keyword_search_history=keyword_value,
    )


def _save_user_behavior_for_logged_in_user(request, product_id, action):
    user_id = request.session.get("logged_in_user_id")
    if not user_id:
        return

    UserBehavior.objects.create(
        id_users_id=user_id,
        id_products_id=product_id,
        action_type_user_behavior=action,
    )


def _sync_cart_to_database(user_id, cart_items):
    with transaction.atomic():
        cart = Cart.objects.filter(id_users_id=user_id).order_by("-id_carts").first()
        if not cart:
            cart = Cart.objects.create(id_users_id=user_id)

        CartItem.objects.filter(id_carts_id=cart.id_carts).delete()

        valid_rows = []
        for item in cart_items:
            try:
                product_id = int(item.get("id"))
                quantity = max(1, int(item.get("quantity", 1)))
            except (TypeError, ValueError):
                continue

            if not Product.objects.filter(id_products=product_id).exists():
                continue

            valid_rows.append(
                CartItem(
                    id_carts_id=cart.id_carts,
                    id_products_id=product_id,
                    quantity_cart_items=quantity,
                )
            )

        if valid_rows:
            CartItem.objects.bulk_create(valid_rows)


def _remove_purchased_items_from_database_cart(user_id, product_ids):
    if not product_ids:
        return

    unique_product_ids = []
    for product_id in product_ids:
        try:
            parsed = int(product_id)
        except (TypeError, ValueError):
            continue
        if parsed not in unique_product_ids:
            unique_product_ids.append(parsed)

    if not unique_product_ids:
        return

    carts = Cart.objects.filter(id_users_id=user_id).values_list("id_carts", flat=True)
    if not carts:
        return

    CartItem.objects.filter(
        id_carts_id__in=list(carts),
        id_products_id__in=unique_product_ids,
    ).delete()


def _get_hot_sale_products(limit=10):
    """Get products with highest discounts for hot sale section"""
    image_exists_subquery = ProductImage.objects.filter(id_products_id=OuterRef("id_products"))
    products_queryset = (
        Product.objects.select_related("id_categories")
        .prefetch_related("images")
        .annotate(has_image=Exists(image_exists_subquery))
    )
    
    discount_context = _build_discount_context()
    hot_sale_products = []
    
    # Get all products and calculate their discounts
    for product in products_queryset[:100]:  # Check first 100 products
        pricing = _get_product_pricing(product, discount_context)
        if pricing["has_discount"]:
            product.id = product.id_products  # Add id for template
            product.primary_image_url = _pick_primary_image(product)
            product.display_price = f"{pricing['final_price']:,.0f}"
            product.old_price = f"{pricing['original_price']:,.0f}"
            product.discount_amount_display = f"{pricing['discount_amount']:,.0f}"
            product.has_discount = True
            product.discount_name = pricing["discount_name"]
            product.discount_percentage = pricing.get("discount_value", 0)
            product.discount_amount = pricing["discount_amount"]
            
            if pricing["discount_type"] == "percent":
                product.discount_badge = f"Giảm {pricing['discount_value']:,.0f}%"
            else:
                product.discount_badge = f"Giảm {pricing['discount_amount']:,.0f}đ"
            
            hot_sale_products.append(product)
    
    # Sort by discount amount (highest first), then by percentage
    hot_sale_products.sort(
        key=lambda x: (x.discount_amount, x.discount_percentage),
        reverse=True
    )
    
    return hot_sale_products[:limit]


def home_page(request):
    selected_category_id = (request.GET.get("category") or "").strip()
    search_query = (request.GET.get("q") or "").strip()
    is_search_result = bool(search_query)
    selected_brand = (request.GET.get("brand") or "").strip()
    selected_price_range = (request.GET.get("price_range") or "").strip()
    has_product_filters = bool(
        selected_category_id or search_query or selected_brand or selected_price_range
    )

    image_exists_subquery = ProductImage.objects.filter(id_products_id=OuterRef("id_products"))
    products_queryset = (
        Product.objects.select_related("id_categories")
        .prefetch_related("images")
        .annotate(has_image=Exists(image_exists_subquery))
    )

    selected_category_name = ""
    if selected_category_id.isdigit():
        products_queryset = products_queryset.filter(id_categories_id=int(selected_category_id))
        selected_category = Category.objects.filter(id_categories=int(selected_category_id)).first()
        if selected_category:
            selected_category_name = selected_category.name_categories

    # Lọc sản phẩm theo khoảng giá và category nếu có
    products_queryset = filter_products_by_price(request)

    # Nếu muốn kết hợp thêm các filter khác (brand, search_query) thì tiếp tục filter trên products_queryset
    # Không dùng order_by("-has_image", ...) vì has_image không phải là field thực tế
    products_queryset = _apply_search_filters(
        products_queryset,
        search_query,
        selected_brand,
        selected_price_range,
    )

    _save_search_history_for_logged_in_user(request, search_query)

    products = (
        products_queryset
        .annotate(total_sold=Coalesce(Sum("orderitem__quantity_order_items"), 0))
        .order_by("-total_sold", "-id_products")[:12]
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

    context = _common_page_context(request)
    context["products"] = products
    context["selected_category_id"] = selected_category_id
    context["selected_category_name"] = selected_category_name
    context["search_query"] = search_query
    context["is_search_result"] = is_search_result
    context["has_product_filters"] = has_product_filters
    context["selected_brand"] = selected_brand
    context["selected_price_range"] = selected_price_range
    
    # Add hot sale products
    context["hot_sale_products"] = _get_hot_sale_products(limit=10)
    
    return render(request, "store/pages/home.html", context)


def _parse_description_to_table(description):
    """
    Parse description format: key: value. key2: value2.
    Returns list of tuples: [(key1, value1), (key2, value2), ...]
    """
    if not description:
        return []
    
    # Split by '.' to get rows
    rows = description.split('.')
    table_data = []
    
    for row in rows:
        row = row.strip()
        if not row:
            continue
        
        # Split by ':' to get key and value
        if ':' in row:
            key, value = row.split(':', 1)
            table_data.append({
                'label': key.strip(),
                'value': value.strip()
            })
    
    return table_data


def product_detail_page(request, product_id):
    try:
        product_obj = (
            Product.objects.select_related("id_categories")
            .prefetch_related("images")
            .get(id_products=product_id)
        )
    except Product.DoesNotExist as exc:
        raise Http404("Product not found") from exc

    _save_user_behavior_for_logged_in_user(request, product_id, "view")

    gallery_images = [
        _normalize_product_image_url(item.image_url)
        for item in product_obj.images.all()
        if _is_valid_product_image_url(item.image_url)
    ]

    fallback_image = _pick_primary_image(product_obj)
    if not gallery_images and fallback_image:
        gallery_images = [fallback_image]

    product_specs = []  # ProductDetail model đã bị xóa, trả về rỗng hoặc thay thế bằng logic khác nếu cần

    sold_count = (
        OrderItem.objects.filter(id_products_id=product_obj.id_products)
        .exclude(id_orders__status_orders="cancelled")
        .aggregate(total_sold=Coalesce(Sum("quantity_order_items"), 0))
        .get("total_sold", 0)
    )

    pricing = _get_product_pricing(product_obj)
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
        "rating_value": "4.5",
    }

    context = _common_page_context(request)
    promotion_context = _build_promotion_context()
    
    # Parse description to table format
    description_table = _parse_description_to_table(product_obj.description)
    
    context.update(
        {
            "product": product,
            "product_specs": product_specs,
            "description_table": description_table,
            "promotions": [
                _format_promotion_text(item)
                for item in promotion_context["promotions_by_code"].values()
            ],
        }
    )
    return render(request, "store/pages/product_detail.html", context)


def cart_page(request):
    context = _common_page_context(request)
    recommended_products = (
        Product.objects.select_related("id_categories")
        .prefetch_related("images")
        .order_by("-id_products")[:4]
    )

    discount_context = _build_discount_context()
    for product in recommended_products:
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

    context["recommended_products"] = recommended_products
    return render(request, "store/pages/cart.html", context)


def viewed_products_page(request):
    user_id = request.session.get("logged_in_user_id")
    if not user_id:
        messages.error(request, "Vui lòng đăng nhập để xem sản phẩm đã xem")
        return redirect("/?auth=login")

    behavior_rows = (
        UserBehavior.objects.filter(
            id_users_id=user_id,
            action_type_user_behavior="view",
        )
        .order_by("-created_at_user_behavior")
        .values_list("id_products_id", flat=True)
    )

    # Keep latest seen products order and remove duplicates.
    product_ids = []
    for product_id in behavior_rows:
        if product_id not in product_ids:
            product_ids.append(product_id)
        if len(product_ids) >= 60:
            break

    products = []
    if product_ids:
        products_map = {
            item.id_products: item
            for item in Product.objects.select_related("id_categories")
            .prefetch_related("images")
            .filter(id_products__in=product_ids)
        }
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
    context.update(
        {
            "products": products,
        }
    )

    return render(request, "store/pages/viewed_products.html", context)


def _safe_redirect_back_home(default_auth_tab="login"):
    return redirect(f"/?auth={default_auth_tab}")


@require_POST
def register_user(request):
    name = (request.POST.get("name_users") or "").strip()
    email = (request.POST.get("email") or "").strip().lower()
    password = request.POST.get("password") or ""
    confirm_password = request.POST.get("confirm_password") or ""
    gender = (request.POST.get("gender_users") or "").strip()
    phone = (request.POST.get("phone_users") or "").strip()
    address = (request.POST.get("address_users") or "").strip()

    missing = []
    if not name:
        missing.append("Họ và tên")
    if not email:
        missing.append("Email")
    if not password:
        missing.append("Mật khẩu")
    if not confirm_password:
        missing.append("Xác nhận mật khẩu")

    if missing:
        messages.error(request, f"Thiếu thông tin bắt buộc: {', '.join(missing)}")
        request.session["register_old"] = {
            "name_users": name,
            "email": email,
            "gender_users": gender,
            "phone_users": phone,
            "address_users": address,
        }
        return _safe_redirect_back_home("register")

    if password != confirm_password:
        messages.error(request, "Mật khẩu xác nhận không khớp")
        request.session["register_old"] = {
            "name_users": name,
            "email": email,
            "gender_users": gender,
            "phone_users": phone,
            "address_users": address,
        }
        return _safe_redirect_back_home("register")

    if len(password) < 6:
        messages.error(request, "Mật khẩu tối thiểu 6 ký tự")
        request.session["register_old"] = {
            "name_users": name,
            "email": email,
            "gender_users": gender,
            "phone_users": phone,
            "address_users": address,
        }
        return _safe_redirect_back_home("register")

    if User.objects.filter(email=email).exists():
        request.session["register_errors"] = {
            "email": "Email đã tồn tại",
        }
        request.session["register_old"] = {
            "name_users": name,
            "email": email,
            "gender_users": gender,
            "phone_users": phone,
            "address_users": address,
        }
        return _safe_redirect_back_home("register")

    user = User.objects.create(
        name_users=name,
        email=email,
        password=make_password(password),
        role="customer",
        gender_users=gender or None,
        phone_users=phone or None,
        address_users=address or None,
    )

    request.session["logged_in_user_id"] = user.id_users
    request.session["logged_in_user_name"] = user.name_users

    messages.success(request, "Đăng ký thành công")
    return redirect("/")


@require_POST
def login_user(request):
    email = (request.POST.get("email") or "").strip().lower()
    password = request.POST.get("password") or ""

    if not email or not password:
        messages.error(request, "Email và mật khẩu là bắt buộc")
        return _safe_redirect_back_home("login")

    user = User.objects.filter(email=email).first()
    if not user:
        messages.error(request, "Tài khoản hoặc mật khẩu không đúng")
        return _safe_redirect_back_home("login")

    hashed_ok = check_password(password, user.password)
    plain_ok = user.password == password

    if not (hashed_ok or plain_ok):
        messages.error(request, "Tài khoản hoặc mật khẩu không đúng")
        return _safe_redirect_back_home("login")

    if plain_ok:
        user.password = make_password(password)
        user.save(update_fields=["password"])

    request.session["logged_in_user_id"] = user.id_users
    request.session["logged_in_user_name"] = user.name_users

    messages.success(request, "Đăng nhập thành công")
    return redirect("/")


@require_POST
def logout_user(request):
    request.session.pop("logged_in_user_id", None)
    request.session.pop("logged_in_user_name", None)
    messages.success(request, "Đã đăng xuất")
    return redirect("/")


def account_info(request):
    """Xem và chỉnh sửa thông tin tài khoản"""
    user_id = request.session.get("logged_in_user_id")
    if not user_id:
        messages.error(request, "Vui lòng đăng nhập để xem tài khoản")
        return redirect("/?auth=login")

    try:
        user = User.objects.get(id_users=user_id)
    except User.DoesNotExist:
        request.session.pop("logged_in_user_id", None)
        request.session.pop("logged_in_user_name", None)
        messages.error(request, "Tài khoản không tồn tại")
        return redirect("/")

    categories = list(Category.objects.all().order_by("name_categories")[:10])
    
    # Thêm brand_list cho mỗi category (nếu cần)
    for cat in categories:
        cat.brand_list = []

    context = {
        "user": user,
        "categories": categories,
        "logged_in_user_name": request.session.get("logged_in_user_name"),
    }

    if request.method == "POST":
        name = (request.POST.get("name_users") or "").strip()
        gender = (request.POST.get("gender_users") or "").strip()
        phone = (request.POST.get("phone_users") or "").strip()
        address = (request.POST.get("address_users") or "").strip()

        if not name:
            messages.error(request, "Tên không được để trống")
            return render(request, "store/pages/account.html", context)

        user.name_users = name
        user.gender_users = gender or None
        user.phone_users = phone or None
        user.address_users = address or None
        user.save()

        request.session["logged_in_user_name"] = user.name_users
        context["logged_in_user_name"] = user.name_users
        messages.success(request, "Cập nhật thông tin thành công")
        return render(request, "store/pages/account.html", context)

    return render(request, "store/pages/account.html", context)


def serialize_product(product):
    primary_image = _pick_primary_image(product)
    pricing = _get_product_pricing(product)

    return {
        "id": product.id_products,
        "name": product.name_products,
        "brand": product.brand,
        "price": pricing["final_price"],
        "base_price": pricing["original_price"],
        "discount_amount": pricing["discount_amount"],
        "has_discount": pricing["has_discount"],
        "discount_name": pricing["discount_name"],
        "stock": product.stock,
        "image": primary_image,
        "description": product.description,
        "category_id": product.id_categories_id,
        "category_name": product.id_categories.name_categories if product.id_categories else None,
    }


@require_GET
def get_products(request):

    products = Product.objects.select_related("id_categories").prefetch_related("images").all()

    data = [serialize_product(product) for product in products]

    return JsonResponse(data, safe=False)


@require_GET
def get_product_detail(request, id):

    try:
        product = Product.objects.select_related("id_categories").get(id_products=id)
    except Product.DoesNotExist:
        return JsonResponse({"error": "Product not found"}, status=404)

    # Đã xóa ProductDetail, chỉ trả về thông tin sản phẩm cơ bản
    data = serialize_product(product)
    return JsonResponse(data)


@require_GET
def get_categories(request):

    categories = Category.objects.all()

    data = []

    for c in categories:
        data.append({
            "id": c.id_categories,
            "name": c.name_categories
        })

    return JsonResponse(data, safe=False)


@require_GET
def search_products(request):

    keyword = (request.GET.get("q") or "").strip()
    selected_category_id = (request.GET.get("category") or "").strip()
    selected_brand = (request.GET.get("brand") or "").strip()
    selected_price_range = (request.GET.get("price_range") or "").strip()

    products = Product.objects.select_related("id_categories").prefetch_related("images")

    if selected_category_id.isdigit():
        products = products.filter(id_categories_id=int(selected_category_id))

    products = _apply_search_filters(products, keyword, selected_brand, selected_price_range)

    if not keyword:
        products = products[:50]

    _save_search_history_for_logged_in_user(request, keyword)

    data = [serialize_product(product) for product in products]

    return JsonResponse(data, safe=False)


@csrf_exempt
@require_POST
def save_behavior(request):

    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    required_fields = ("product_id", "action")
    missing_fields = [field for field in required_fields if not body.get(field)]

    if missing_fields:
        return JsonResponse(
            {"error": f"Missing required fields: {', '.join(missing_fields)}"},
            status=400,
        )

    try:
        user_id = int(body.get("user_id") or request.session.get("logged_in_user_id") or 0)
        product_id = int(body["product_id"])
    except (TypeError, ValueError):
        return JsonResponse({"error": "user_id and product_id must be integers"}, status=400)

    if not user_id:
        return JsonResponse({"error": "Not authenticated"}, status=401)

    UserBehavior.objects.create(
        id_users_id=user_id,
        id_products_id=product_id,
        action_type_user_behavior=body["action"]
    )

    return JsonResponse({"status": "ok"})


@require_POST
def save_cart_to_session(request):
    """Lưu giỏ hàng từ frontend vào session backend"""
    user_id = request.session.get("logged_in_user_id")
    if not user_id:
        return JsonResponse({"error": "Not authenticated"}, status=401)

    try:
        body = json.loads(request.body)
        cart_items = body.get("cart_items", [])
        sync_db = bool(body.get("sync_db", True))
        
        # Lưu vào session
        request.session["cart_items"] = cart_items
        request.session.modified = True

        # Đồng bộ vào database để hiển thị trong Admin (Cart / CartItem)
        if sync_db:
            _sync_cart_to_database(user_id, cart_items)
        
        return JsonResponse({"status": "ok"})
    except (json.JSONDecodeError, KeyError) as e:
        return JsonResponse({"error": str(e)}, status=400)


def checkout(request):
    """Trang checkout - xác nhận đơn hàng"""
    user_id = request.session.get("logged_in_user_id")
    if not user_id:
        messages.error(request, "Vui lòng đăng nhập để tiếp tục")
        return redirect("/?auth=login")

    try:
        user = User.objects.get(id_users=user_id)
    except User.DoesNotExist:
        request.session.pop("logged_in_user_id", None)
        messages.error(request, "Tài khoản không tồn tại")
        return redirect("/")

    # Lấy cart items từ session
    cart_items_session = request.session.get("cart_items", [])
    
    # Nếu không có cart items, return error
    if not cart_items_session:
        messages.warning(request, "Giỏ hàng trống")
        return redirect("/cart/")

    discount_context = _build_discount_context()
    promotion_context = _build_promotion_context()

    # Tính tổng tiền theo 2 tầng: discount sản phẩm -> promotion code
    subtotal_original = Decimal("0")
    subtotal_after_product_discount = Decimal("0")
    total_product_discount = Decimal("0")
    cart_items_with_product = []
    
    for item in cart_items_session:
        try:
            product = Product.objects.get(id_products=item["id"])
            quantity = item.get("quantity", 1)
            quantity = max(1, int(quantity))

            pricing = _get_product_pricing(product, discount_context)
            unit_base_price = pricing["original_price"]
            unit_final_price = pricing["final_price"]
            line_original = unit_base_price * quantity
            line_after_product_discount = unit_final_price * quantity
            line_discount = line_original - line_after_product_discount

            subtotal_original += line_original
            subtotal_after_product_discount += line_after_product_discount
            total_product_discount += line_discount
            
            cart_items_with_product.append({
                "product": {
                    **product.__dict__,
                    "name": product.name_products,
                    "image": _pick_primary_image(product),
                    "unit_base_price": unit_base_price,
                    "unit_final_price": unit_final_price,
                    "discount_name": pricing["discount_name"],
                    "has_discount": pricing["has_discount"],
                },
                "quantity": quantity,
                "product_id": product.id_products,
                "line_total_original": line_original,
                "line_total_after_product_discount": line_after_product_discount,
                "line_product_discount": line_discount,
                "display_price": f"{unit_final_price:,.0f}",
                "display_original_price": f"{unit_base_price:,.0f}",
                "display_subtotal": f"{line_after_product_discount:,.0f}",
                "display_original_subtotal": f"{line_original:,.0f}",
                "display_line_discount": f"{line_discount:,.0f}",
            })
        except Product.DoesNotExist:
            continue

    apply_promotion_value = (request.POST.get("apply_promotion") or "").strip()
    entered_promotion_code = (request.POST.get("promotion_code") or "").strip()
    if not entered_promotion_code and apply_promotion_value and apply_promotion_value != "1":
        entered_promotion_code = apply_promotion_value
    if not entered_promotion_code:
        entered_promotion_code = (request.GET.get("promo") or "").strip()
    promotion_result = _evaluate_promotion_code(entered_promotion_code, cart_items_with_product, promotion_context)
    promotion_discount_amount = promotion_result["amount"] if promotion_result["is_applied"] else Decimal("0")
    final_total = max(Decimal("0"), subtotal_after_product_discount - promotion_discount_amount)

    available_promotions = []
    unavailable_promotions = []
    for promo in promotion_context["promotions_by_code"].values():
        preview = _evaluate_promotion_code(promo.code, cart_items_with_product, promotion_context)
        promotion_data = {
            "code": promo.code,
            "text": _format_promotion_text(promo),
            "is_eligible": preview["is_applied"],
            "preview_discount": f"{preview['amount']:,.0f}",
            "reason": preview.get("reason", ""),
            "message": preview.get("message", ""),
        }
        if preview["is_applied"]:
            available_promotions.append(promotion_data)
        else:
            unavailable_promotions.append(promotion_data)

    saved_promotion_codes = _get_saved_promotion_codes(request)
    saved_promotions = []
    for code in saved_promotion_codes:
        promo = promotion_context["promotions_by_code"].get(code)
        if not promo:
            continue

        preview = _evaluate_promotion_code(promo.code, cart_items_with_product, promotion_context)
        saved_promotions.append(
            {
                "code": promo.code,
                "text": _format_promotion_text(promo),
                "is_eligible": preview["is_applied"],
                "preview_discount": f"{preview['amount']:,.0f}",
                "reason": preview.get("reason", ""),
                "message": preview.get("message", ""),
                "save_button_label": "Đã lưu",
            }
        )

    if len(saved_promotions) != len(saved_promotion_codes):
        _set_saved_promotion_codes(request, [item["code"] for item in saved_promotions])
    
    context = _common_page_context(request)
    context.update({
        "user": user,
        "cart_items": cart_items_with_product,
        "display_subtotal_original": f"{subtotal_original:,.0f}",
        "display_product_discount": f"{total_product_discount:,.0f}",
        "display_subtotal_after_product_discount": f"{subtotal_after_product_discount:,.0f}",
        "display_promotion_discount": f"{promotion_discount_amount:,.0f}",
        "display_total": f"{final_total:,.0f}",
        "promotion_code": entered_promotion_code,
        "promotion_message": promotion_result.get("message", ""),
        "promotion_applied": promotion_result.get("is_applied", False),
        "saved_promotions": saved_promotions,
        "available_promotions": available_promotions,
        "unavailable_promotions": unavailable_promotions,
    })

    if request.method == "POST":
        if request.POST.get("apply_promotion"):
            return render(request, "store/pages/checkout.html", context)

        # Xử lý tạo đơn hàng
        phone = (request.POST.get("phone") or "").strip()
        address = (request.POST.get("address") or "").strip()
        # notes = (request.POST.get("notes") or "").strip()  # Không sử dụng

        if not phone or not address:
            messages.error(request, "Vui lòng nhập đầy đủ thông tin")
            return render(request, "store/pages/checkout.html", context)

        product_quantity_map = {}
        for item in cart_items_with_product:
            product_id = item["product_id"]
            product_quantity_map[product_id] = product_quantity_map.get(product_id, 0) + item["quantity"]

        with transaction.atomic():
            locked_products = {
                product.id_products: product
                for product in Product.objects.select_for_update().filter(
                    id_products__in=product_quantity_map.keys()
                )
            }

            stock_errors = []
            for product_id, required_qty in product_quantity_map.items():
                product = locked_products.get(product_id)
                if not product:
                    stock_errors.append(f"Sản phẩm #{product_id} không tồn tại")
                    continue

                if product.stock is None:
                    stock_errors.append(f"{product.name_products}: chưa khai báo tồn kho")
                    continue

                if product.stock < required_qty:
                    stock_errors.append(
                        f"{product.name_products}: còn {product.stock}, cần {required_qty}"
                    )

            if stock_errors:
                messages.error(request, "Không thể đặt hàng do tồn kho không đủ: " + "; ".join(stock_errors))
                return render(request, "store/pages/checkout.html", context)

            # Tạo Order theo đúng schema hiện tại của model Order
            order = Order.objects.create(
                id_users_id=user_id,
                total_price_orders=final_total,
                status_orders="pending",
            )

            # Tạo OrderItems
            purchased_product_ids = []
            for item in cart_items_with_product:
                OrderItem.objects.create(
                    id_orders_id=order.id_orders,
                    id_products_id=item["product_id"],
                    quantity_order_items=item["quantity"],
                    price_order_items=item["product"]["unit_final_price"],
                )
                purchased_product_ids.append(item["product_id"])

            # Trừ tồn kho ngay sau khi tạo đơn
            for product_id, required_qty in product_quantity_map.items():
                Product.objects.filter(id_products=product_id).update(stock=F("stock") - required_qty)

            _remove_purchased_items_from_database_cart(user_id, purchased_product_ids)

        # Xóa cart khỏi session
        request.session.pop("cart_items", None)
        request.session["clear_cart_order_id"] = order.id_orders
        request.session["clear_cart_product_ids"] = purchased_product_ids
        
        messages.success(request, "Đơn hàng tạo thành công! Mã đơn hàng: #{}".format(order.id_orders))
        return redirect("order_detail", order_id=order.id_orders)

    return render(request, "store/pages/checkout.html", context)


def order_list(request):
    """Danh sách đơn hàng của user"""
    user_id = request.session.get("logged_in_user_id")
    if not user_id:
        messages.error(request, "Vui lòng đăng nhập")
        return redirect("/?auth=login")

    try:
        user = User.objects.get(id_users=user_id)
    except User.DoesNotExist:
        request.session.pop("logged_in_user_id", None)
        return redirect("/")

    selected_status = (request.GET.get("status") or "all").strip().lower()
    allowed_statuses = {"pending", "confirmed", "shipping", "completed", "cancelled"}
    if selected_status not in allowed_statuses and selected_status != "all":
        selected_status = "all"

    base_orders = Order.objects.filter(id_users_id=user_id)

    if selected_status == "all":
        orders = base_orders.order_by("-created_at_orders")
    else:
        orders = base_orders.filter(status_orders=selected_status).order_by("-created_at_orders")
    
    # Format display
    for order in orders:
        order.display_total = f"{order.total_price_orders:,.0f}"
        order.status_display = {
            "pending": "Chờ xử lý",
            "confirmed": "Đã xác nhận",
            "shipping": "Đang giao",
            "completed": "Hoàn thành",
            "cancelled": "Đã hủy"
        }.get(order.status_orders, order.status_orders)

    context = _common_page_context(request)
    status_tabs = [
        {"code": "all", "label": "Tất cả", "count": base_orders.count()},
        {"code": "pending", "label": "Chờ xử lý", "count": base_orders.filter(status_orders="pending").count()},
        {"code": "confirmed", "label": "Đã xác nhận", "count": base_orders.filter(status_orders="confirmed").count()},
        {"code": "shipping", "label": "Đang giao", "count": base_orders.filter(status_orders="shipping").count()},
        {"code": "completed", "label": "Hoàn thành", "count": base_orders.filter(status_orders="completed").count()},
        {"code": "cancelled", "label": "Đã hủy", "count": base_orders.filter(status_orders="cancelled").count()},
    ]

    context.update({
        "user": user,
        "orders": orders,
        "selected_status": selected_status,
        "status_tabs": status_tabs,
        "logged_in_user_name": request.session.get("logged_in_user_name"),
    })

    return render(request, "store/pages/order_list.html", context)


def order_detail(request, order_id):
    """Chi tiết đơn hàng"""
    user_id = request.session.get("logged_in_user_id")
    if not user_id:
        messages.error(request, "Vui lòng đăng nhập")
        return redirect("/?auth=login")

    try:
        order = Order.objects.get(id_orders=order_id, id_users_id=user_id)
    except Order.DoesNotExist:
        messages.error(request, "Đơn hàng không tồn tại")
        return redirect("order_list")

    # Lấy order items
    order_items = OrderItem.objects.filter(id_orders_id=order_id).select_related("id_products")
    
    for item in order_items:
        item.display_price = f"{item.price_order_items:,.0f}"
        item.subtotal = item.quantity_order_items * item.price_order_items
        item.display_subtotal = f"{item.subtotal:,.0f}"
        item.product_image_url = _pick_primary_image(item.id_products)

    subtotal_after_product_discount = sum((item.subtotal for item in order_items), Decimal("0"))
    promotion_discount_amount = max(Decimal("0"), subtotal_after_product_discount - Decimal(order.total_price_orders or 0))

    order.display_total = f"{order.total_price_orders:,.0f}"
    order.display_subtotal_after_product_discount = f"{subtotal_after_product_discount:,.0f}"
    order.display_promotion_discount = f"{promotion_discount_amount:,.0f}"
    order.status_display = {
        "pending": "Chờ xử lý",
        "confirmed": "Đã xác nhận",
        "shipping": "Đang giao",
        "completed": "Hoàn thành",
        "cancelled": "Đã hủy"
    }.get(order.status_orders, order.status_orders)

    try:
        user = User.objects.get(id_users=user_id)
    except User.DoesNotExist:
        user = None

    context = _common_page_context(request)

    clear_cart_product_ids = []
    clear_cart_order_id = request.session.get("clear_cart_order_id")
    if clear_cart_order_id == order_id:
        clear_cart_product_ids = request.session.pop("clear_cart_product_ids", [])
        request.session.pop("clear_cart_order_id", None)

    context.update({
        "user": user,
        "order": order,
        "order_items": order_items,
        "logged_in_user_name": request.session.get("logged_in_user_name"),
        "clear_cart_product_ids_json": json.dumps(clear_cart_product_ids),
    })

    return render(request, "store/pages/order_detail.html", context)

def filter_products_by_price(request):
    price_range = (request.GET.get("price_range") or "").strip()
    category_id = (request.GET.get("category") or "").strip()
    products = Product.objects.all()

    # Lọc theo category nếu có
    if category_id.isdigit():
        products = products.filter(id_categories_id=int(category_id))

    # Lọc chính xác theo khoảng giá
    if price_range == "lt5":
        products = products.filter(price__gte=0, price__lt=5000000)
    elif price_range == "lt15":
        products = products.filter(price__gte=0, price__lt=15000000)
    elif price_range == "15-20":
        products = products.filter(price__gte=15000000, price__lte=20000000)
    elif price_range == "gt20":
        products = products.filter(price__gt=20000000)
    else:
        # Nếu không chọn price_range thì không lọc theo giá
        pass

    return products

