"""Payment views."""

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from .views import (
    VNPAY_PENDING_PAYMENT_KEY,
    _build_cart_items_with_pricing,
    _create_order_from_checkout_data,
    _verify_vnpay_signature,
)


@require_GET
def vnpay_return(request):
    pending_payment = request.session.get(VNPAY_PENDING_PAYMENT_KEY) or {}
    if not pending_payment:
        messages.error(request, "Không tìm thấy giao dịch ví điện tử đang chờ xử lý.")
        return redirect("checkout")

    query_data = {key: value for key, value in request.GET.items()}
    if not _verify_vnpay_signature(query_data):
        request.session.pop(VNPAY_PENDING_PAYMENT_KEY, None)
        messages.error(request, "Xác thực chữ ký thanh toán thất bại.")
        return redirect("checkout")

    txn_ref = request.GET.get("vnp_TxnRef", "")
    if txn_ref != pending_payment.get("txn_ref"):
        messages.error(request, "Mã giao dịch không khớp.")
        return redirect("checkout")

    response_code = request.GET.get("vnp_ResponseCode", "")
    transaction_status = request.GET.get("vnp_TransactionStatus", "")
    if response_code != "00" or transaction_status != "00":
        request.session.pop(VNPAY_PENDING_PAYMENT_KEY, None)
        messages.error(request, f"Thanh toán không thành công (VNPAY code: {response_code or 'N/A'}).")
        return redirect("checkout")

    user_id = int(pending_payment.get("user_id") or 0)
    cart_items = pending_payment.get("cart_items") or []
    promotion_code = (pending_payment.get("promotion_code") or "").strip()
    phone = (pending_payment.get("phone") or "").strip()
    address = (pending_payment.get("address") or "").strip()
    selected_address_id = pending_payment.get("selected_address_id")

    cart_pricing = _build_cart_items_with_pricing(cart_items)
    cart_items_with_product = cart_pricing["cart_items_with_product"]
    subtotal_after_product_discount = cart_pricing["subtotal_after_product_discount"]

    if not cart_items_with_product:
        request.session.pop(VNPAY_PENDING_PAYMENT_KEY, None)
        messages.error(request, "Không còn sản phẩm hợp lệ để tạo đơn hàng.")
        return redirect("cart_page")

    order_result = _create_order_from_checkout_data(
        request,
        user_id,
        cart_items_with_product,
        subtotal_after_product_discount,
        promotion_code,
        address=address,
        phone=phone,
        selected_address_id=selected_address_id,
    )
    if not order_result.get("ok"):
        messages.error(request, order_result.get("message", "Không thể tạo đơn hàng sau thanh toán."))
        return redirect("checkout")

    order = order_result["order"]
    request.session.pop(VNPAY_PENDING_PAYMENT_KEY, None)
    request.session.pop("cart_items", None)
    request.session["clear_cart_client"] = True
    messages.success(request, f"Thanh toán thành công. Mã đơn hàng: #{order.id_orders}")
    return redirect("order_detail", order_id=order.id_orders)


@csrf_exempt
@require_GET
def vnpay_ipn(request):
    query_data = {key: value for key, value in request.GET.items()}
    if not _verify_vnpay_signature(query_data):
        return JsonResponse({"RspCode": "97", "Message": "Invalid Checksum"})

    return JsonResponse({"RspCode": "00", "Message": "Confirm Success"})


__all__ = ["vnpay_return", "vnpay_ipn"]
