"""Cart and checkout views."""

from decimal import Decimal
import json

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from django.utils import timezone

from .models import User, UserAddress
from .views_account import _enforce_active_user, _enforce_password_change
from .views_utils_shared import (
    _build_cart_items_with_pricing,
    _build_promotion_context,
    _build_vnpay_payment_url,
    _common_page_context,
    _create_order_from_checkout_data,
    _evaluate_promotion_code,
    _format_promotion_text,
    _get_cart_items_from_database,
    _get_personalized_products_for_home,
    _get_saved_promotion_codes,
    _get_user_promotion_usage_map,
    _set_saved_promotion_codes,
    _sync_cart_to_database,
    _verify_vnpay_signature,
    VNPAY_PENDING_PAYMENT_KEY,
)


def cart_page(request):
    user_id = request.session.get("logged_in_user_id")
    if not user_id:
        messages.error(request, "Vui lòng đăng nhập để xem giỏ hàng")
        return redirect("/?auth=login")

    locked_response = _enforce_active_user(request)
    if locked_response:
        return locked_response

    password_response = _enforce_password_change(request)
    if password_response:
        return password_response

    context = _common_page_context(request)
    recommended_products, recommendation_source = _get_personalized_products_for_home(request, limit=6)
    context["recommended_products"] = recommended_products
    context["recommendation_source"] = recommendation_source
    return render(request, "store/pages/cart.html", context)


@csrf_exempt
@require_POST
def save_cart_to_session(request):
    """Lưu giỏ hàng từ frontend vào session backend."""
    user_id = request.session.get("logged_in_user_id")
    if not user_id:
        return JsonResponse({"error": "Not authenticated"}, status=401)

    locked_response = _enforce_active_user(request, json_response=True)
    if locked_response:
        return locked_response

    password_response = _enforce_password_change(request, json_response=True)
    if password_response:
        return password_response

    try:
        body = json.loads(request.body)
        cart_items = body.get("cart_items", [])
        sync_db = bool(body.get("sync_db", True))

        request.session["cart_items"] = cart_items
        request.session.modified = True

        if sync_db:
            _sync_cart_to_database(user_id, cart_items)

        return JsonResponse({"status": "ok"})
    except (json.JSONDecodeError, KeyError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_GET
def load_cart_from_database(request):
    user_id = request.session.get("logged_in_user_id")
    if not user_id:
        return JsonResponse({"error": "Not authenticated"}, status=401)

    locked_response = _enforce_active_user(request, json_response=True)
    if locked_response:
        return locked_response

    password_response = _enforce_password_change(request, json_response=True)
    if password_response:
        return password_response

    cart_items = _get_cart_items_from_database(user_id)
    request.session["cart_items"] = cart_items
    request.session.modified = True
    return JsonResponse({"cart_items": cart_items, "count": len(cart_items)})


@require_http_methods(["GET", "POST"])
def checkout(request):
    """Trang checkout - xác nhận đơn hàng."""
    user_id = request.session.get("logged_in_user_id")
    if not user_id:
        messages.error(request, "Vui lòng đăng nhập để tiếp tục")
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
        messages.error(request, "Tài khoản không tồn tại")
        return redirect("/")

    cart_items_session = request.session.get("cart_items", [])
    if not cart_items_session:
        messages.warning(request, "Giỏ hàng trống")
        return redirect("/cart/")

    promotion_context = _build_promotion_context()
    cart_pricing = _build_cart_items_with_pricing(cart_items_session)
    cart_items_with_product = cart_pricing["cart_items_with_product"]
    subtotal_original = cart_pricing["subtotal_original"]
    subtotal_after_product_discount = cart_pricing["subtotal_after_product_discount"]
    total_product_discount = cart_pricing["total_product_discount"]

    apply_promotion_value = (request.POST.get("apply_promotion") or "").strip()
    entered_promotion_code = (request.POST.get("promotion_code") or "").strip()
    if not entered_promotion_code and apply_promotion_value and apply_promotion_value != "1":
        entered_promotion_code = apply_promotion_value
    if not entered_promotion_code:
        entered_promotion_code = (request.GET.get("promo") or "").strip()

    promotion_ids = [promo.id_promotions for promo in promotion_context["promotions_by_code"].values()]
    user_promotion_usage_map = _get_user_promotion_usage_map(user_id, promotion_ids)

    promotion_result = _evaluate_promotion_code(
        entered_promotion_code,
        cart_items_with_product,
        promotion_context,
        user_id=user_id,
        user_promotion_usage_map=user_promotion_usage_map,
    )
    promotion_discount_amount = promotion_result["amount"] if promotion_result["is_applied"] else Decimal("0")
    final_total = max(Decimal("0"), subtotal_after_product_discount - promotion_discount_amount)

    user_addresses = list(
        UserAddress.objects.filter(id_users_id=user_id).order_by("-is_default", "-created_at_addresses", "-id_user_addresses")
    )

    selected_address_id = (request.POST.get("selected_address_id") or request.GET.get("address_id") or "").strip()
    selected_address_obj = None
    if selected_address_id.isdigit():
        selected_address_obj = next((addr for addr in user_addresses if addr.id_user_addresses == int(selected_address_id)), None)

    initial_phone = selected_address_obj.phone_address if selected_address_obj and selected_address_obj.phone_address else (user.phone_users or "")
    initial_address = selected_address_obj.full_address if selected_address_obj else (user.address_users or "")

    available_promotions = []
    unavailable_promotions = []
    for promo in promotion_context["promotions_by_code"].values():
        preview = _evaluate_promotion_code(
            promo.code,
            cart_items_with_product,
            promotion_context,
            user_id=user_id,
            user_promotion_usage_map=user_promotion_usage_map,
        )
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

        preview = _evaluate_promotion_code(
            promo.code,
            cart_items_with_product,
            promotion_context,
            user_id=user_id,
            user_promotion_usage_map=user_promotion_usage_map,
        )
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
    context.update(
        {
            "user": user,
            "cart_items": cart_items_with_product,
            "display_subtotal_original": f"{subtotal_original:,.0f}",
            "display_product_discount": f"{total_product_discount:,.0f}",
            "display_subtotal_after_product_discount": f"{subtotal_after_product_discount:,.0f}",
            "display_promotion_discount": f"{promotion_discount_amount:,.0f}",
            "display_total": f"{final_total:,.0f}",
            "user_addresses": user_addresses,
            "selected_address_id": selected_address_id,
            "selected_shipping_phone": initial_phone,
            "selected_shipping_address": initial_address,
            "promotion_code": entered_promotion_code,
            "promotion_message": promotion_result.get("message", ""),
            "promotion_applied": promotion_result.get("is_applied", False),
            "saved_promotions": saved_promotions,
            "available_promotions": available_promotions,
            "unavailable_promotions": unavailable_promotions,
        }
    )

    if request.method == "POST":
        if request.POST.get("apply_promotion"):
            return render(request, "store/pages/checkout.html", context)

        payment_method = (request.POST.get("payment_method") or "cod").strip().lower()
        posted_selected_address_id = (request.POST.get("selected_address_id") or "").strip()
        phone = (request.POST.get("phone") or "").strip()
        address = (request.POST.get("address") or "").strip()
        notes = (request.POST.get("notes") or "").strip()

        selected_checkout_address = None
        if posted_selected_address_id.isdigit():
            selected_checkout_address = next((addr for addr in user_addresses if addr.id_user_addresses == int(posted_selected_address_id)), None)

        if selected_checkout_address:
            address = selected_checkout_address.full_address
            phone = (selected_checkout_address.phone_address or phone or "").strip()
            posted_selected_address_id = str(selected_checkout_address.id_user_addresses)
        else:
            posted_selected_address_id = None

        if not phone or not address:
            messages.error(request, "Vui lòng nhập đầy đủ thông tin")
            return render(request, "store/pages/checkout.html", context)

        if payment_method == "wallet":
            txn_ref = f"{user_id}{timezone.localtime().strftime('%Y%m%d%H%M%S%f')}"[-20:]
            request.session[VNPAY_PENDING_PAYMENT_KEY] = {
                "txn_ref": txn_ref,
                "user_id": user_id,
                "cart_items": cart_items_session,
                "promotion_code": entered_promotion_code,
                "phone": phone,
                "address": address,
                "selected_address_id": posted_selected_address_id,
                "notes": notes,
                "expected_amount": int(final_total),
                "created_at": timezone.localtime().isoformat(),
            }
            request.session.modified = True

            payment_url = _build_vnpay_payment_url(
                request,
                amount_vnd=int(final_total),
                txn_ref=txn_ref,
                order_info=f"Thanh toan don hang user {user_id}",
            )
            return redirect(payment_url)

        order_result = _create_order_from_checkout_data(
            request,
            user_id,
            cart_items_with_product,
            subtotal_after_product_discount,
            entered_promotion_code,
            address=address,
            phone=phone,
            selected_address_id=posted_selected_address_id,
        )
        if not order_result.get("ok"):
            messages.error(request, order_result.get("message", "Không thể tạo đơn hàng."))
            return render(request, "store/pages/checkout.html", context)

        order = order_result["order"]
        request.session.pop("cart_items", None)
        request.session["clear_cart_client"] = True
        messages.success(request, "Đơn hàng tạo thành công! Mã đơn hàng: #{}".format(order.id_orders))
        return redirect("order_detail", order_id=order.id_orders)

    return render(request, "store/pages/checkout.html", context)


__all__ = ["cart_page", "checkout", "save_cart_to_session", "load_cart_from_database"]
