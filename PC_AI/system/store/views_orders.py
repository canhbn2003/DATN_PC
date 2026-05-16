"""Order views."""

from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .models import Order, OrderItem, Review, User
from .views_account import _enforce_active_user, _enforce_password_change
from .views_utils_shared import ORDER_CANCEL_REASONS, _common_page_context, _pick_primary_image


@require_http_methods(["GET", "POST"])
def order_list(request):
    """Danh sách đơn hàng của user."""
    user_id = request.session.get("logged_in_user_id")
    if not user_id:
        messages.error(request, "Vui lòng đăng nhập")
        return redirect("/?auth=login")

    locked_response = _enforce_active_user(request)
    if locked_response:
        return locked_response

    password_response = _enforce_password_change(request)
    if password_response:
        return password_response

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

    for order in orders:
        order.display_total = f"{order.total_price_orders:,.0f}"
        order.status_display = {
            "pending": "Chờ xử lý",
            "confirmed": "Đã xác nhận",
            "shipping": "Đang giao",
            "completed": "Hoàn thành",
            "cancelled": "Đã hủy",
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

    context.update(
        {
            "user": user,
            "orders": orders,
            "selected_status": selected_status,
            "status_tabs": status_tabs,
            "logged_in_user_name": request.session.get("logged_in_user_name"),
        }
    )

    return render(request, "store/pages/order_list.html", context)


@require_http_methods(["GET", "POST"])
def order_detail(request, order_id):
    """Chi tiết đơn hàng."""
    user_id = request.session.get("logged_in_user_id")
    if not user_id:
        messages.error(request, "Vui lòng đăng nhập")
        return redirect("/?auth=login")

    locked_response = _enforce_active_user(request)
    if locked_response:
        return locked_response

    password_response = _enforce_password_change(request)
    if password_response:
        return password_response

    try:
        order = Order.objects.get(id_orders=order_id, id_users_id=user_id)
    except Order.DoesNotExist:
        messages.error(request, "Đơn hàng không tồn tại")
        return redirect("order_list")

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()
        if action != "cancel_order":
            messages.error(request, "Yêu cầu không hợp lệ.")
            return redirect("order_detail", order_id=order_id)

        if order.status_orders != "pending":
            messages.error(request, "Chỉ có thể hủy đơn khi trạng thái đang là Chờ xử lý.")
            return redirect("order_detail", order_id=order_id)

        cancel_reason = (request.POST.get("cancel_reason") or "").strip()
        cancel_reason_other = (request.POST.get("cancel_reason_other") or "").strip()

        if cancel_reason not in ORDER_CANCEL_REASONS:
            messages.error(request, "Vui lòng chọn lý do hủy đơn.")
            return redirect("order_detail", order_id=order_id)

        if cancel_reason == "Lý do khác":
            if not cancel_reason_other:
                messages.error(request, "Vui lòng nhập lý do hủy cụ thể.")
                return redirect("order_detail", order_id=order_id)
            selected_reason_text = cancel_reason_other
        else:
            selected_reason_text = cancel_reason

        with transaction.atomic():
            order.status_orders = "cancelled"
            order.save(update_fields=["status_orders"])

            order_items = OrderItem.objects.filter(id_orders_id=order_id)
            for item in order_items:
                product = item.id_products
                if product:
                    product.stock = (product.stock or 0) + item.quantity_order_items
                    product.save(update_fields=["stock"])

        cancel_reason_map = request.session.get("order_cancel_reason_map", {})
        cancel_reason_map[str(order_id)] = selected_reason_text
        request.session["order_cancel_reason_map"] = cancel_reason_map

        messages.success(request, f"Đã hủy đơn hàng #{order.id_orders}. Lý do: {selected_reason_text}")
        return redirect("order_detail", order_id=order_id)

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
        "cancelled": "Đã hủy",
    }.get(order.status_orders, order.status_orders)

    try:
        user = User.objects.get(id_users=user_id)
    except User.DoesNotExist:
        user = None

    review_prompt_items = []
    if order.status_orders == "completed":
        order_product_ids = [item.id_products_id for item in order_items if item.id_products_id]
        reviewed_ids = set(
            Review.objects.filter(id_users_id=user_id, id_products_id__in=order_product_ids)
            .values_list("id_products_id", flat=True)
        )
        for item in order_items:
            if item.id_products_id in reviewed_ids:
                continue
            review_prompt_items.append(
                {
                    "product_id": item.id_products_id,
                    "product_name": item.id_products.name_products if item.id_products else f"Sản phẩm #{item.id_products_id}",
                }
            )

    context = _common_page_context(request)
    context.update(
        {
            "user": user,
            "order": order,
            "shipping_address": order.id_user_addresses,
            "order_items": order_items,
            "logged_in_user_name": request.session.get("logged_in_user_name"),
            "can_cancel_order": order.status_orders == "pending",
            "order_cancel_reasons": ORDER_CANCEL_REASONS,
            "order_cancel_reason_text": (request.session.get("order_cancel_reason_map", {}) or {}).get(str(order_id), ""),
            "review_prompt_items": review_prompt_items,
            "show_review_prompt": bool(review_prompt_items),
        }
    )

    return render(request, "store/pages/order_detail.html", context)


__all__ = ["order_list", "order_detail"]
