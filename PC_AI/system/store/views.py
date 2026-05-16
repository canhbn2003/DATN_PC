import json
import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from decimal import Decimal
import requests
from urllib.parse import urlparse

from urllib.parse import urlencode
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.hashers import check_password, make_password
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Exists, F, OuterRef, Q, Sum
from django.db.models.functions import Coalesce
from django.http import JsonResponse, Http404
from django.shortcuts import redirect, render
from django.core.paginator import Paginator
from django.utils import timezone
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from .ai.recommenders import item as item_recommender
from .ai.recommenders import user as user_recommender
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
    Review,
    Promotion,
    PromotionProduct,
    SearchHistory,
    User,
    UserAddress,
    UserBehavior,
    UserPromotion,
    WebsiteSettings,
)
from django.views.decorators.csrf import csrf_exempt
from django.core.mail import send_mail
import random

# Xử lý gửi OTP khi quên mật khẩu
@require_POST
@csrf_exempt
def forgot_password(request):
    email = request.POST.get('email')
    if not email:
        return JsonResponse({'success': False, 'message': 'Vui lòng nhập email.'}, status=400)

    # Kiểm tra email có tồn tại trong hệ thống không
    try:
        User.objects.get(email=email)
    except User.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Email không tồn tại. Vui lòng kiểm tra lại.'}, status=200)

    # Sinh mã OTP 6 số
    otp = str(random.randint(100000, 999999))
    # Lưu OTP vào session (hoặc có thể lưu vào DB nếu muốn)
    request.session['reset_password_otp'] = otp
    request.session['reset_password_email'] = email
    request.session.set_expiry(300)  # OTP có hiệu lực 5 phút

    # Gửi email
    subject = 'Mã xác nhận đặt lại mật khẩu LTC Computer'
    message = f'Mã xác nhận đặt lại mật khẩu của bạn là: {otp}\nMã có hiệu lực trong 5 phút.'
    from_email = settings.DEFAULT_FROM_EMAIL if hasattr(settings, 'DEFAULT_FROM_EMAIL') else None
    try:
        send_mail(subject, message, from_email, [email], fail_silently=False)
    except Exception as e:
        return JsonResponse({'success': False, 'message': f'Lỗi gửi email: {str(e)}'}, status=500)

    return JsonResponse({'success': True, 'message': 'Đã gửi mã xác nhận về email của bạn.'})

OLD_PRICE_MULTIPLIER = Decimal("1.15")


SAVED_PROMOTION_CODES_SESSION_KEY = "saved_promotion_codes"
BEHAVIOR_SESSION_KEY = "session_behavior_events"
BEHAVIOR_SESSION_MAX_EVENTS = 120
SESSION_BEHAVIOR_ACTION_WEIGHTS = {
    "view": 1.0,
    "add_to_cart": 3.0,
    "purchase": 6.0,
}
BEHAVIOR_ALLOWED_ACTIONS = {"view", "add_to_cart", "purchase"}
BEHAVIOR_ACTION_ALIASES = {
    "buy_now": "add_to_cart",
}

ORDER_CANCEL_REASONS = [
    "Thay đổi ý định mua hàng",
    "Đặt nhầm sản phẩm",
    "Muốn thay đổi địa chỉ/điện thoại nhận hàng",
    "Tìm thấy giá tốt hơn",
    "Muốn thay đổi phương thức thanh toán",
    "Lý do khác",
]

VNPAY_PENDING_PAYMENT_KEY = "vnpay_pending_payment"
GEMINI_CHAT_ENDPOINT = "https://generativelanguage.googleapis.com/v1/models/{model}:generateContent"
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


def _list_gemini_models(api_key):
    try:
        response = requests.get(
            f"https://generativelanguage.googleapis.com/v1/models?key={api_key}",
            timeout=30,
        )
    except requests.RequestException as exc:
        return None, str(exc)

    if response.status_code >= 400:
        return None, response.text[:1000]

    try:
        payload = response.json()
    except Exception as exc:
        return None, str(exc)

    models = payload.get("models") or []
    available = []
    for item in models:
        name = item.get("name")
        methods = item.get("supportedGenerationMethods") or []
        if not name:
            continue
        if "generateContent" in methods:
            available.append(name.replace("models/", ""))

    return available, None


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
@require_POST
@csrf_exempt
def verify_otp(request):
    otp_input = request.POST.get('otp')
    otp_session = request.session.get('reset_password_otp')
    email_session = request.session.get('reset_password_email')
    if not otp_input or not otp_session or not email_session:
        return JsonResponse({'success': False, 'message': 'Thông tin xác thực không hợp lệ.'}, status=400)
    if otp_input != otp_session:
        return JsonResponse({'success': False, 'message': 'Mã xác nhận không đúng hoặc đã hết hạn.'}, status=200)
    # Đánh dấu đã xác thực OTP thành công (có thể dùng cho bước đổi mật khẩu tiếp theo)
    request.session['otp_verified'] = True
    return JsonResponse({'success': True, 'message': 'Xác thực mã OTP thành công.'})
@require_POST
@csrf_exempt
def reset_password(request):
    # Chỉ cho phép nếu đã xác thực OTP
    if not request.session.get('otp_verified'):
        return JsonResponse({'success': False, 'message': 'Bạn chưa xác thực mã OTP.'}, status=400)
    email = request.session.get('reset_password_email')
    new_password = request.POST.get('new_password')
    confirm_password = request.POST.get('confirm_password')
    if not new_password or not confirm_password:
        return JsonResponse({'success': False, 'message': 'Vui lòng nhập đầy đủ mật khẩu.'}, status=400)
    if new_password != confirm_password:
        return JsonResponse({'success': False, 'message': 'Mật khẩu nhập lại không khớp.'}, status=400)
    if len(new_password) < 6:
        return JsonResponse({'success': False, 'message': 'Mật khẩu phải có ít nhất 6 ký tự.'}, status=400)
    try:
        user = User.objects.get(email=email)
        user.password = make_password(new_password)
        user.save(update_fields=["password"])
        # Xoá session liên quan đến reset password
        for key in ['reset_password_otp', 'reset_password_email', 'otp_verified']:
            if key in request.session:
                del request.session[key]
        return JsonResponse({'success': True, 'message': 'Đổi mật khẩu thành công. Bạn có thể đăng nhập bằng mật khẩu mới.'})
    except User.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Tài khoản không tồn tại.'}, status=400)

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
        "discount_start_date": best_discount.start_date if best_discount else None,
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


def _build_promotion_context_with_upcoming():
    """
    Fetch both active and upcoming promotions.
    Returns active and upcoming promotions separately for display.
    """
    now = timezone.now()
    
    # Fetch active promotions
    active_promotions = list(
        Promotion.objects.filter(
            status=True,
            start_date__lte=now,
            end_date__gte=now,
            used_count__lt=F("usage_limit"),
        )
    )
    
    # Fetch upcoming promotions (not yet started)
    upcoming_promotions = list(
        Promotion.objects.filter(
            status=True,
            start_date__gt=now,
        )
    )
    
    # Combine all promotions for building the product map
    all_promotions = active_promotions + upcoming_promotions
    all_promotions_list = list(all_promotions)
    
    if not all_promotions_list:
        return {
            "active_promotions_by_code": {},
            "upcoming_promotions_by_code": {},
            "promotion_product_map": {},
        }
    
    # Build mappings
    active_promotions_by_code = {item.code.upper(): item for item in active_promotions}
    upcoming_promotions_by_code = {item.code.upper(): item for item in upcoming_promotions}
    
    promotion_ids = [item.id_promotions for item in all_promotions_list]
    
    promotion_product_map = {}
    rows = PromotionProduct.objects.filter(id_promotions_id__in=promotion_ids).values_list(
        "id_promotions_id",
        "id_products_id",
    )
    for promotion_id, product_id in rows:
        promotion_product_map.setdefault(promotion_id, set()).add(product_id)
    
    return {
        "active_promotions_by_code": active_promotions_by_code,
        "upcoming_promotions_by_code": upcoming_promotions_by_code,
        "promotions_by_code": active_promotions_by_code,  # Keep for backward compatibility
        "promotion_product_map": promotion_product_map,
    }


def _get_user_promotion_usage_map(user_id, promotion_ids=None):
    if not user_id:
        return {}

    queryset = UserPromotion.objects.filter(id_users_id=user_id)
    if promotion_ids:
        queryset = queryset.filter(id_promotions_id__in=promotion_ids)

    return {
        int(promotion_id): int(used_count or 0)
        for promotion_id, used_count in queryset.values_list("id_promotions_id", "used_count")
    }


def _evaluate_promotion_code(
    promotion_code,
    cart_lines,
    promotion_context=None,
    user_id=None,
    user_promotion_usage_map=None,
):
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

    per_user_limit = int(promotion.usage_limit_per_user or 1)
    if per_user_limit < 1:
        per_user_limit = 1

    if user_id:
        if user_promotion_usage_map is None:
            user_used_count = (
                UserPromotion.objects.filter(
                    id_users_id=user_id,
                    id_promotions_id=promotion.id_promotions,
                )
                .values_list("used_count", flat=True)
                .first()
                or 0
            )
        else:
            user_used_count = int(user_promotion_usage_map.get(promotion.id_promotions, 0) or 0)

        if int(user_used_count) >= per_user_limit:
            return {
                "is_applied": False,
                "code": code,
                "amount": Decimal("0"),
                "message": f"Mã {code} đã được sử dụng cho tài khoản này.",
                "reason": "already_used",
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


def _get_saved_promotion_codes_session_key(request):
    user_id = request.session.get("logged_in_user_id")
    if user_id:
        return f"{SAVED_PROMOTION_CODES_SESSION_KEY}_user_{int(user_id)}"

    return f"{SAVED_PROMOTION_CODES_SESSION_KEY}_guest"


def _get_saved_promotion_codes(request):
    session_key = _get_saved_promotion_codes_session_key(request)
    raw_codes = request.session.get(session_key, [])
    if not isinstance(raw_codes, (list, tuple)):
        raw_codes = []

    cleaned_codes = []
    for code in raw_codes:
        normalized_code = (str(code) or "").strip().upper()
        if normalized_code and normalized_code not in cleaned_codes:
            cleaned_codes.append(normalized_code)

    if cleaned_codes != list(raw_codes):
        request.session[session_key] = cleaned_codes
        request.session.modified = True

    return cleaned_codes


def _set_saved_promotion_codes(request, codes):
    session_key = _get_saved_promotion_codes_session_key(request)
    cleaned_codes = []
    for code in codes:
        normalized_code = (str(code) or "").strip().upper()
        if normalized_code and normalized_code not in cleaned_codes:
            cleaned_codes.append(normalized_code)

    request.session[session_key] = cleaned_codes
    request.session.modified = True
    return cleaned_codes


def _build_promotion_card_data(promotion, promotion_context, saved_codes=None, cart_lines=None):
    saved_codes = set(saved_codes or [])
    eligible_product_ids = promotion_context["promotion_product_map"].get(promotion.id_promotions, set())
    scope_text = "Áp dụng cho toàn bộ đơn hàng"
    if eligible_product_ids:
        scope_text = f"Áp dụng cho {len(eligible_product_ids)} sản phẩm"
        # fetch product names for preview (limit to 5)
        try:
            product_names = list(
                Product.objects.filter(id_products__in=list(eligible_product_ids))
                .values_list("name_products", flat=True)[:5]
            )
        except Exception:
            product_names = []
        applies_preview = ", ".join([str(name) for name in product_names if name])
        if applies_preview:
            if len(eligible_product_ids) > len(product_names):
                applies_preview = f"{applies_preview}, ..."
            applies_to_text = f"Áp dụng cho: {applies_preview}"
        else:
            applies_to_text = ""
    else:
        applies_to_text = ""

    usage_left = max(int(promotion.usage_limit or 0) - int(promotion.used_count or 0), 0)
    card_data = {
        "code": promotion.code,
        "text": _format_promotion_text(promotion),
        "scope_text": scope_text,
        "applies_to_text": applies_to_text,
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


def _get_public_categories_queryset():
    return Category.objects.filter(status=1, is_visible=True)


def _get_public_products_queryset():
    # Public queryset: includes products whose category is active/visible.
    # Do NOT filter by product.status here so discontinued products remain visible
    # in lists/pages/detail/cart. Recommender code will explicitly exclude discontinued products.
    return Product.objects.select_related("id_categories").prefetch_related("images").filter(
        id_categories__status=1,
        id_categories__is_visible=True,
    )


@require_http_methods(["GET", "POST"])
def flash_sale_page(request):
    promotion_context = _build_promotion_context_with_upcoming()
    saved_codes = _get_saved_promotion_codes(request)

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()
        code = (request.POST.get("code") or "").strip().upper()

        if not code:
            messages.error(request, "Vui lòng chọn mã giảm giá")
            return redirect("flash_sale_page")

        # Check both active and upcoming promotions
        promotion = (
            promotion_context["active_promotions_by_code"].get(code)
            or promotion_context["upcoming_promotions_by_code"].get(code)
        )
        
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

    # Build active promotion cards
    active_promotions = [
        _build_promotion_card_data(promotion, promotion_context, saved_codes=saved_codes)
        for promotion in sorted(
            promotion_context["active_promotions_by_code"].values(),
            key=lambda item: (item.end_date, item.id_promotions),
        )
    ]
    
    # Build upcoming promotion cards
    upcoming_promotions = [
        _build_promotion_card_data(promotion, promotion_context, saved_codes=saved_codes)
        for promotion in sorted(
            promotion_context["upcoming_promotions_by_code"].values(),
            key=lambda item: (item.start_date, item.id_promotions),
        )
    ]

    context = _common_page_context(request)
    context.update(
        {
            "promotions": active_promotions,  # Keep for backward compatibility
            "active_promotions": active_promotions,
            "upcoming_promotions": upcoming_promotions,
            "saved_promotion_codes": saved_codes,
            "saved_promotion_count": len(saved_codes),
        }
    )
    return render(request, "store/pages/flash_sale.html", context)


def _common_page_context(request):
    categories = list(_get_public_categories_queryset().order_by("name_categories")[:10])
    category_ids = [item.id_categories for item in categories]

    brand_map = {category_id: [] for category_id in category_ids}
    if category_ids:
        brand_rows = (
            _get_public_products_queryset().filter(id_categories_id__in=category_ids)
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
    session_user_role = (request.session.get("logged_in_user_role") or "").strip().lower()
    session_user_id = request.session.get("logged_in_user_id")

    if session_user_id and not session_user_role:
        role_value = (
            User.objects.filter(id_users=session_user_id)
            .values_list("role", flat=True)
            .first()
            or ""
        )
        session_user_role = str(role_value).strip().lower()
        request.session["logged_in_user_role"] = session_user_role

    website_settings = WebsiteSettings.objects.order_by("-updated_at", "-id_settings").first()

    def _split_banner_values(raw_value):
        if not raw_value:
            return []

        if isinstance(raw_value, (list, tuple)):
            chunks = [str(item) for item in raw_value]
        else:
            chunks = re.split(r"[\n,;|]+", str(raw_value))

        cleaned = []
        for item in chunks:
            value = (item or "").strip()
            if value and value not in cleaned:
                cleaned.append(value)
        return cleaned

    def _normalize_website_asset_url(raw_url):
        if not raw_url:
            return ""

        value = str(raw_url).strip().replace("\\", "/")
        if not value:
            return ""

        if value.startswith(("http://", "https://", "//", "data:")):
            return value

        if value.startswith("/"):
            return value

        media_url = (settings.MEDIA_URL or "/media/").rstrip("/") + "/"
        media_url_no_slash = media_url.lstrip("/")

        if value.lower().startswith(media_url_no_slash.lower()):
            return "/" + value

        if value.lower().startswith("media/"):
            value = value[6:]

        return media_url + value.lstrip("/")

    def _parse_banner_layout(raw_value):
        if not raw_value:
            return {"main": [], "side": [], "bottom": []}

        serialized = (str(raw_value) or "").strip()
        if not serialized:
            return {"main": [], "side": [], "bottom": []}

        compact_match = re.findall(r"(?:^|;)([msb])=([^;]*)", serialized)
        if compact_match:
            group_map = {"m": [], "s": [], "b": []}
            for key, packed_values in compact_match:
                group_map[key] = _split_banner_values(str(packed_values).replace("|", "\n"))
            return {
                "main": group_map["m"][:3],
                "side": group_map["s"][:2],
                "bottom": group_map["b"][:3],
            }

        try:
            parsed = json.loads(serialized)
            if isinstance(parsed, dict):
                return {
                    "main": _split_banner_values(parsed.get("main", []))[:3],
                    "side": _split_banner_values(parsed.get("side", []))[:2],
                    "bottom": _split_banner_values(parsed.get("bottom", []))[:3],
                }
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

        legacy_values = _split_banner_values(serialized)
        return {
            "main": legacy_values[:3],
            "side": legacy_values[3:5],
            "bottom": legacy_values[5:8],
        }

    banner_layout = _parse_banner_layout(getattr(website_settings, "banner_url", ""))
    website_main_banner_urls = [_normalize_website_asset_url(item) for item in banner_layout["main"] if item]
    website_side_banner_urls = [_normalize_website_asset_url(item) for item in banner_layout["side"] if item]
    website_bottom_banner_urls = [_normalize_website_asset_url(item) for item in banner_layout["bottom"] if item]
    website_banner_urls = website_main_banner_urls + website_side_banner_urls + website_bottom_banner_urls
    website_name_display = (getattr(website_settings, "website_name", "") or "LTC Computer").strip() or "LTC Computer"
    # compute lockout timestamp for front-end countdown (ms since epoch)
    login_locked_until_ts = None
    locked_iso = request.session.get("login_locked_until")
    if locked_iso:
        try:
            locked_dt = timezone.datetime.fromisoformat(locked_iso)
            if timezone.is_naive(locked_dt):
                locked_dt = timezone.make_aware(locked_dt, timezone.get_current_timezone())
            login_locked_until_ts = int(locked_dt.timestamp() * 1000)
        except Exception:
            login_locked_until_ts = None

    return {
        "categories": categories,
        "auth_tab": auth_tab,
        "selected_category_id": (request.GET.get("category") or "").strip(),
        "search_query": (request.GET.get("q") or "").strip(),
        "selected_brand": selected_brand,
        "selected_price_range": selected_price_range,
        "price_ranges": price_ranges,
        "logged_in_user_id": session_user_id,
        "logged_in_user_name": request.session.get("logged_in_user_name"),
        "logged_in_user_role": session_user_role,
        "is_admin_user": session_user_role == "admin",
        "clear_cart_client": request.session.pop("clear_cart_client", False),
        "register_errors": request.session.pop("register_errors", {}),
        "register_old": request.session.pop("register_old", {}),
        "website_settings": website_settings,
        "website_name_display": website_name_display,
        "website_logo_url": getattr(website_settings, "logo_url", "") if website_settings else "",
        "website_main_banner_urls": website_main_banner_urls,
        "website_side_banner_urls": website_side_banner_urls,
        "website_bottom_banner_urls": website_bottom_banner_urls,
        "website_banner_urls": website_banner_urls,
        # lockout timestamp in milliseconds since epoch (client-friendly for JS countdown)
        "login_locked_until_ts": login_locked_until_ts,
    }


def _format_notification_timestamp(dt_value):
    if not dt_value:
        return ""

    try:
        return timezone.localtime(dt_value).isoformat()
    except (TypeError, ValueError):
        return ""


@dataclass
class NotificationViewModel:
    id: str
    title: str
    message: str
    type: str
    target_url: str
    created_at: object
    sale_score: float | None = None
    sale_price: float | None = None

    def to_payload(self):
        created_at_iso = _format_notification_timestamp(self.created_at)
        payload = {
            "Id": self.id,
            "Title": self.title,
            "Message": self.message,
            "Type": self.type,
            "TargetUrl": self.target_url,
            "CreatedAt": created_at_iso,
            "id": self.id,
            "title": self.title,
            "message": self.message,
            "type": self.type,
            "targetUrl": self.target_url,
            "createdAt": created_at_iso,
            "link": self.target_url,
            "time": created_at_iso,
        }

        if self.sale_score is not None:
            payload["saleScore"] = self.sale_score
        if self.sale_price is not None:
            payload["salePrice"] = self.sale_price

        return payload


def _build_notification_items(request, limit=20):
    now = timezone.now()
    notifications: list[NotificationViewModel] = []

    active_promotions = (
        Promotion.objects.filter(
            status=True,
            start_date__lte=now,
            end_date__gte=now,
            used_count__lt=F("usage_limit"),
        )
        .order_by("-start_date", "-id_promotions")[:6]
    )

    for promotion in active_promotions:
        notifications.append(
            NotificationViewModel(
                id=f"promotion-{promotion.id_promotions}",
                title=f"Mã giảm giá mới: {promotion.code}",
                message=_format_promotion_text(promotion),
                type="promotion",
                target_url="/flash-sale/",
                created_at=promotion.start_date or promotion.created_at or now,
            )
        )

    hot_sale_products = _get_hot_sale_products(limit=4)
    for product in hot_sale_products:
        discount_badge = getattr(product, "discount_badge", "")
        discount_name = getattr(product, "discount_name", "")
        message_parts = [part for part in [discount_badge, discount_name] if part]
        sale_score = None
        if getattr(product, "discount_type", "") == "percent":
            sale_score = float(getattr(product, "discount_percentage", 0) or 0)
        else:
            sale_score = float(getattr(product, "discount_amount", 0) or 0)

        created_at = (
            getattr(product, "discount_start_date", None)
            or getattr(product, "created_at_products", None)
            or now
        )

        notifications.append(
            NotificationViewModel(
                id=f"sale-{product.id_products}",
                title=f"Giá tốt hôm nay: {product.name_products}",
                message=" · ".join(message_parts) or "Sản phẩm đang có giá tốt nhất.",
                type="sale",
                target_url=f"/products/{product.id_products}/",
                created_at=created_at,
                sale_score=sale_score,
                sale_price=float(getattr(product, "final_price", 0) or 0),
            )
        )

    user_id = request.session.get("logged_in_user_id")
    if user_id:
        order_rows = (
            Order.objects.filter(id_users_id=user_id)
            .order_by("-created_at_orders", "-id_orders")[:8]
        )

        status_titles = {
            "pending": "Đặt hàng thành công",
            "confirmed": "Đơn hàng đã được admin duyệt",
            "shipping": "Đơn hàng đang được giao",
            "completed": "Đơn hàng đã hoàn thành",
            "cancelled": "Đơn hàng đã hủy",
        }
        status_messages = {
            "pending": "Chúng tôi đã ghi nhận đơn hàng và sẽ xử lý sớm nhất.",
            "confirmed": "Đơn hàng đã được duyệt và đang chuẩn bị giao.",
            "shipping": "Đơn hàng đang trên đường đến bạn.",
            "completed": "Cảm ơn bạn đã mua hàng tại cửa hàng.",
            "cancelled": "Đơn hàng đã được hủy theo yêu cầu.",
        }

        for order in order_rows:
            status_code = (order.status_orders or "pending").strip().lower()
            status_timestamp = order.created_at_orders
            if status_code != "pending":
                status_timestamp = now
            notifications.append(
                NotificationViewModel(
                    id=f"order-{order.id_orders}-{status_code}",
                    title=status_titles.get(status_code, "Cập nhật đơn hàng"),
                    message=status_messages.get(status_code, "Đơn hàng có cập nhật mới."),
                    type="order",
                    target_url=f"/api/orders/{order.id_orders}/",
                    created_at=status_timestamp,
                )
            )

    notifications.sort(key=lambda item: item.created_at or now, reverse=True)
    trimmed = notifications[: max(1, int(limit or 20))]
    return [item.to_payload() for item in trimmed]


@require_GET
def notifications_api(request):
    items = _build_notification_items(request, limit=20)
    return JsonResponse({"items": items})


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


def _apply_product_sorting(products_queryset, sort_code="best_selling"):
    normalized = (sort_code or "").strip().lower()
    if normalized == "price_asc":
        return products_queryset.order_by("price", "-id_products")
    if normalized == "price_desc":
        return products_queryset.order_by("-price", "-id_products")
    return products_queryset.order_by("-total_sold", "-id_products")


def _user_has_completed_purchase(user_id, product_id):
    if not user_id or not product_id:
        return False

    return OrderItem.objects.filter(
        id_products_id=product_id,
        id_orders__id_users_id=user_id,
        id_orders__status_orders="completed",
    ).exists()


def _get_product_review_summary(product_id):
    stats = Review.objects.filter(id_products_id=product_id, status="Hiển thị").aggregate(
        avg_rating=Avg("rating"),
        total_reviews=Count("id_reviews"),
    )

    avg_rating = float(stats.get("avg_rating") or 0)
    total_reviews = int(stats.get("total_reviews") or 0)
    star_count = min(5, max(0, int(round(avg_rating))))

    return {
        "avg_rating": round(avg_rating, 1),
        "total_reviews": total_reviews,
        "star_count": star_count,
    }


def _get_visible_product_reviews(product_id, limit=20):
    return list(
        Review.objects.select_related("id_users")
        .filter(id_products_id=product_id, status="Hiển thị")
        .order_by("-created_at_reviews", "-id_reviews")[: max(1, int(limit or 20))]
    )


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


def _get_session_behavior_events(request):
    raw_events = request.session.get(BEHAVIOR_SESSION_KEY, [])
    if not isinstance(raw_events, list):
        raw_events = []

    cleaned_events = []
    for event in raw_events:
        if not isinstance(event, dict):
            continue

        try:
            product_id = int(event.get("product_id") or 0)
        except (TypeError, ValueError):
            continue

        normalized_action = BEHAVIOR_ACTION_ALIASES.get((event.get("action") or "").strip(), (event.get("action") or "").strip())
        if normalized_action not in BEHAVIOR_ALLOWED_ACTIONS or product_id <= 0:
            continue

        cleaned_events.append(
            {
                "product_id": product_id,
                "action": normalized_action,
                "timestamp": event.get("timestamp") or "",
            }
        )

    if cleaned_events != raw_events:
        request.session[BEHAVIOR_SESSION_KEY] = cleaned_events[-BEHAVIOR_SESSION_MAX_EVENTS:]
        request.session.modified = True

    return cleaned_events


def _append_session_behavior_event(request, product_id, action):
    normalized_action = BEHAVIOR_ACTION_ALIASES.get((action or "").strip(), (action or "").strip())
    if normalized_action not in BEHAVIOR_ALLOWED_ACTIONS:
        return

    try:
        product_id_value = int(product_id)
    except (TypeError, ValueError):
        return

    events = _get_session_behavior_events(request)
    events.append(
        {
            "product_id": product_id_value,
            "action": normalized_action,
            "timestamp": timezone.now().isoformat(),
        }
    )

    request.session[BEHAVIOR_SESSION_KEY] = events[-BEHAVIOR_SESSION_MAX_EVENTS:]
    request.session.modified = True


def _get_session_recommendation_product_ids(request, limit=12):
    events = _get_session_behavior_events(request)
    if not events:
        return []

    scored_products = {}
    latest_positions = {}
    decay = 0.92
    total_events = len(events)

    for index, event in enumerate(events):
        product_id = int(event["product_id"])
        action = event["action"]
        base_weight = SESSION_BEHAVIOR_ACTION_WEIGHTS.get(action, 1.0)
        recency_weight = decay ** (total_events - index - 1)
        score = base_weight * recency_weight

        scored_products[product_id] = scored_products.get(product_id, 0.0) + score
        latest_positions[product_id] = index

    ranked_product_ids = [
        product_id
        for product_id, _ in sorted(
            scored_products.items(),
            key=lambda item: (item[1], latest_positions.get(item[0], 0), item[0]),
            reverse=True,
        )
    ]

    return ranked_product_ids[:limit]


def _extract_ai_chat_budget(question):
    cleaned_question = str(question or "").lower()
    normalized = cleaned_question.replace(",", ".")

    patterns = [
        r"(\d+(?:\.\d+)?)\s*(?:trieu|tr|triệu)",
        r"(\d+(?:\.\d+)?)\s*(?:m|m\.)\b",
        r"(\d+(?:\.\d+)?)\s*(?:k|nghin|nghìn)\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue

        value = float(match.group(1))
        if "k" in pattern or "nghin" in pattern or "nghìn" in pattern:
            return int(value * 1_000)
        if "m" in pattern and "trieu" not in pattern and "tr" not in pattern:
            return int(value * 1_000_000)
        return int(value * 1_000_000)

    value_match = re.search(r"(\d{5,9})", normalized)
    if value_match:
        return int(value_match.group(1))

    return None


def _extract_ai_chat_intent(question):
    cleaned_question = str(question or "").lower()
    intent_keywords = {
        "gaming": ["game", "gaming", "chơi game", "fps", "2k", "4k", "esport", "valorant", "lol", "cs2", "pubg"],
        "graphics": ["đồ họa", "do hoa", "render", "edit", "dựng phim", "duong phim", "after effects", "premiere", "3d", "designer"],
        "office": ["văn phòng", "van phong", "office", "học tập", "hoc tap", "làm việc", "lam viec", "kế toán", "ke toan"],
        "stream": ["stream", "livestream", "ghi hình", "capture"],
        "balanced": ["đa năng", "da nang", "all round", "cân bằng", "can bang"],
    }

    for intent, keywords in intent_keywords.items():
        if any(keyword in cleaned_question for keyword in keywords):
            return intent

    return "balanced"


def _build_ai_chat_data_context(question, limit=60):
    cleaned_question = str(question or "").strip().lower()
    tokens = [token for token in re.findall(r"[a-zA-Z0-9_]+", cleaned_question) if len(token) >= 2][:10]
    budget = _extract_ai_chat_budget(cleaned_question)
    intent = _extract_ai_chat_intent(cleaned_question)

    intent_category_rules = {
        "gaming": ["CPU", "GPU", "RAM", "SSD", "PSU", "CASE", "COOLING", "MAINBOARD"],
        "graphics": ["CPU", "GPU", "RAM", "SSD", "PSU", "CASE", "COOLING", "MAINBOARD"],
        "office": ["CPU", "RAM", "SSD", "PSU", "CASE", "COOLING", "MAINBOARD"],
        "stream": ["CPU", "GPU", "RAM", "SSD", "PSU", "CASE", "COOLING", "MAINBOARD"],
        "balanced": ["CPU", "GPU", "RAM", "SSD", "PSU", "CASE", "COOLING", "MAINBOARD"],
    }
    required_categories = intent_category_rules.get(intent, intent_category_rules["balanced"])
    base_limit = max(18, min(int(limit or 60), 120))

    products_qs = _get_public_products_queryset().annotate(total_sold=Coalesce(Sum("orderitem__quantity_order_items"), 0))
    candidate_products = []
    seen_product_ids = set()

    def add_products(items):
        for product in items:
            product_id = getattr(product, "id_products", None)
            if product_id is None or product_id in seen_product_ids:
                continue
            candidate_products.append(product)
            seen_product_ids.add(product_id)
            if len(candidate_products) >= base_limit:
                return

    if tokens:
        token_query = Q()
        for token in tokens:
            token_query |= Q(name_products__icontains=token)
            token_query |= Q(brand__icontains=token)
            token_query |= Q(description__icontains=token)
            token_query |= Q(id_categories__name_categories__icontains=token)
        token_matches = list(products_qs.filter(token_query).order_by("-total_sold", "-id_products")[: base_limit * 2])
        add_products(token_matches)

    popular_products = list(products_qs.order_by("-total_sold", "-id_products")[: base_limit * 2])
    add_products(popular_products)

    newest_products = list(products_qs.order_by("-id_products")[: base_limit * 2])
    add_products(newest_products)

    if len(candidate_products) < base_limit:
        categories = list(_get_public_categories_queryset().order_by("name_categories").values_list("id_categories", flat=True))
        for category_id in categories:
            if len(candidate_products) >= base_limit:
                break
            category_products = list(products_qs.filter(id_categories_id=category_id).order_by("-total_sold", "-id_products")[:4])
            add_products(category_products)

    if not candidate_products:
        candidate_products = list(products_qs.order_by("-id_products")[:base_limit])

    scored_products = []
    for product in candidate_products:
        pricing = _get_product_pricing(product)
        final_price = float(pricing.get("final_price") or 0)
        name_lower = (product.name_products or "").lower()
        brand_lower = (product.brand or "").lower()
        description_lower = (product.description or "").lower()
        category_name = product.id_categories.name_categories if product.id_categories else "Khac"
        category_lower = category_name.lower()
        relevance = 0.0

        for token in tokens:
            if token in name_lower:
                relevance += 8.0
            if token in brand_lower:
                relevance += 4.0
            if token in description_lower:
                relevance += 2.0
            if token in category_lower:
                relevance += 3.0

        if tokens and all(token in (name_lower + " " + brand_lower + " " + category_lower) for token in tokens[:4]):
            relevance += 12.0

        if category_name and category_name.upper() in required_categories:
            relevance += 6.0

        if budget and final_price > 0:
            distance_ratio = abs(final_price - float(budget)) / max(float(budget), 1.0)
            relevance += max(0.0, 28.0 - (distance_ratio * 45.0))

        stock = int(product.stock or 0)
        if stock > 0:
            relevance += 5.0
        else:
            relevance -= 2.0

        popularity = min(float(getattr(product, "total_sold", 0) or 0), 5000.0) / 400.0
        scored_products.append((relevance + popularity, int(getattr(product, "total_sold", 0) or 0), product))

    scored_products.sort(key=lambda row: (row[0], row[1], row[2].id_products), reverse=True)
    products = [row[2] for row in scored_products[:base_limit]]

    available_categories = set(
        name.strip()
        for name in _get_public_products_queryset()
        .values_list("id_categories__name_categories", flat=True)
        .distinct()
        if name
    )
    lines = []
    for product in products:
        pricing = _get_product_pricing(product)
        category_name = product.id_categories.name_categories if product.id_categories else "Khac"
        lines.append(
            " | ".join(
                [
                    f"ID:{product.id_products}",
                    f"Ten:{(product.name_products or '').strip()}",
                    f"ThuongHieu:{(product.brand or '').strip() or 'N/A'}",
                    f"DanhMuc:{category_name}",
                    f"Gia:{int(pricing['final_price']):,} VND",
                    f"TonKho:{int(product.stock or 0)}",
                ]
            )
        )

    categories = list(_get_public_categories_queryset().order_by("name_categories").values_list("name_categories", flat=True))
    categories_text = ", ".join([str(name).strip() for name in categories if str(name).strip()])

    return {
        "products_text": "\n".join(lines),
        "categories_text": categories_text,
        "products_count": len(lines),
        "catalog_count": products_qs.count(),
        "budget": budget,
        "intent": intent,
        "required_categories": required_categories,
        "available_categories": sorted(list(available_categories)),
    }


def _load_products_by_ordered_ids(product_ids):
    if not product_ids:
        return []

    products_map = {
        item.id_products: item
        for item in _get_public_products_queryset()
        .filter(id_products__in=product_ids)
    }

    return [products_map[product_id] for product_id in product_ids if product_id in products_map]


def _save_user_behavior_for_logged_in_user(request, product_id, action):
    user_id = request.session.get("logged_in_user_id")
    normalized_action = BEHAVIOR_ACTION_ALIASES.get(action, action)
    if normalized_action not in BEHAVIOR_ALLOWED_ACTIONS:
        return

    _append_session_behavior_event(request, product_id, normalized_action)

    if not user_id:
        return

    UserBehavior.objects.create(
        id_users_id=user_id,
        id_products_id=product_id,
        action_type_user_behavior=normalized_action,
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


def _get_cart_items_from_database(user_id):
    cart = Cart.objects.filter(id_users_id=user_id).order_by("-id_carts").first()
    if not cart:
        return []

    cart_rows = list(
        CartItem.objects.filter(id_carts_id=cart.id_carts)
        .values_list("id_products_id", "quantity_cart_items")
    )
    if not cart_rows:
        return []

    ordered_product_ids = []
    quantity_map = {}
    for product_id, quantity in cart_rows:
        try:
            parsed_product_id = int(product_id)
            parsed_quantity = max(1, int(quantity or 1))
        except (TypeError, ValueError):
            continue

        if parsed_product_id not in ordered_product_ids:
            ordered_product_ids.append(parsed_product_id)
        quantity_map[parsed_product_id] = parsed_quantity

    if not ordered_product_ids:
        return []

    products_map = {
        product.id_products: product
        for product in Product.objects.select_related("id_categories")
        .prefetch_related("images")
        .filter(id_products__in=ordered_product_ids)
    }

    discount_context = _build_discount_context()
    items = []
    for product_id in ordered_product_ids:
        product = products_map.get(product_id)
        if not product:
            continue

        pricing = _get_product_pricing(product, discount_context)
        items.append(
            {
                "id": product.id_products,
                "name": product.name_products,
                "brand": product.brand or "N/A",
                "category": product.id_categories.name_categories if product.id_categories else "Khác",
                "image": _pick_primary_image(product) or "",
                "price": float(pricing["final_price"]),
                "quantity": quantity_map.get(product_id, 1),
                "status": product.status,
                "is_discontinued": getattr(product, "is_discontinued", False),
            }
        )

    return items


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


def _get_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "127.0.0.1")


def _build_vnpay_payment_url(request, amount_vnd, txn_ref, order_info):
    create_date = timezone.localtime().strftime("%Y%m%d%H%M%S")
    expire_date = (timezone.localtime() + timezone.timedelta(minutes=15)).strftime("%Y%m%d%H%M%S")

    params = {
        "vnp_Version": "2.1.0",
        "vnp_Command": "pay",
        "vnp_TmnCode": settings.VNPAY_TMN_CODE,
        "vnp_Amount": str(int(amount_vnd) * 100),
        "vnp_CreateDate": create_date,
        "vnp_ExpireDate": expire_date,
        "vnp_CurrCode": "VND",
        "vnp_IpAddr": _get_client_ip(request),
        "vnp_Locale": "vn",
        "vnp_OrderInfo": order_info,
        "vnp_OrderType": "other",
        "vnp_ReturnUrl": settings.VNPAY_RETURN_URL,
        "vnp_TxnRef": txn_ref,
    }

    query_string = urlencode(sorted(params.items()))
    secure_hash = hmac.new(
        settings.VNPAY_HASH_SECRET.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()

    return f"{settings.VNPAY_PAYMENT_URL}?{query_string}&vnp_SecureHash={secure_hash}"


def _verify_vnpay_signature(query_params):
    secure_hash = query_params.get("vnp_SecureHash", "")
    if not secure_hash:
        return False

    signing_params = {
        key: value
        for key, value in query_params.items()
        if key not in ("vnp_SecureHash", "vnp_SecureHashType")
    }
    signed_data = urlencode(sorted(signing_params.items()))
    expected_hash = hmac.new(
        settings.VNPAY_HASH_SECRET.encode("utf-8"),
        signed_data.encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()

    return hmac.compare_digest(expected_hash, secure_hash)


def _build_cart_items_with_pricing(cart_items_session):
    discount_context = _build_discount_context()
    subtotal_original = Decimal("0")
    subtotal_after_product_discount = Decimal("0")
    total_product_discount = Decimal("0")
    cart_items_with_product = []

    for item in cart_items_session:
        try:
            product = Product.objects.get(id_products=item["id"])
            quantity = max(1, int(item.get("quantity", 1)))
        except (Product.DoesNotExist, TypeError, ValueError, KeyError):
            continue

        pricing = _get_product_pricing(product, discount_context)
        unit_base_price = pricing["original_price"]
        unit_final_price = pricing["final_price"]
        line_original = unit_base_price * quantity
        line_after_product_discount = unit_final_price * quantity
        line_discount = line_original - line_after_product_discount

        subtotal_original += line_original
        subtotal_after_product_discount += line_after_product_discount
        total_product_discount += line_discount

        cart_items_with_product.append(
            {
                "product": {
                    **product.__dict__,
                    "name": product.name_products,
                    "image": _pick_primary_image(product),
                    "unit_base_price": unit_base_price,
                    "unit_final_price": unit_final_price,
                    "discount_name": pricing["discount_name"],
                    "has_discount": pricing["has_discount"],
                    "status": product.status,
                    "is_discontinued": product.is_discontinued,
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
                "status": product.status,
                "is_discontinued": product.is_discontinued,
            }
        )

    return {
        "cart_items_with_product": cart_items_with_product,
        "subtotal_original": subtotal_original,
        "subtotal_after_product_discount": subtotal_after_product_discount,
        "total_product_discount": total_product_discount,
    }


def _create_order_from_checkout_data(
    request,
    user_id,
    cart_items_with_product,
    subtotal_after_product_discount,
    entered_promotion_code,
    address=None,
    phone=None,
    selected_address_id=None,
):
    saved_promotion_codes = _get_saved_promotion_codes(request)

    product_quantity_map = {}
    for item in cart_items_with_product:
        product_id = item["product_id"]
        product_quantity_map[product_id] = product_quantity_map.get(product_id, 0) + item["quantity"]

    with transaction.atomic():
        locked_products = {
            product.id_products: product
            for product in Product.objects.select_for_update().filter(id_products__in=product_quantity_map.keys())
        }

        stock_errors = []
        discontinued_errors = []
        for product_id, required_qty in product_quantity_map.items():
            product = locked_products.get(product_id)
            if not product:
                stock_errors.append(f"Sản phẩm #{product_id} không tồn tại")
                continue
            if product.is_discontinued:
                discontinued_errors.append(f"{product.name_products}: sản phẩm đã ngừng kinh doanh")
                continue
            if product.stock is None:
                stock_errors.append(f"{product.name_products}: chưa khai báo tồn kho")
                continue
            if product.stock < required_qty:
                stock_errors.append(f"{product.name_products}: còn {product.stock}, cần {required_qty}")

        if discontinued_errors:
            return {"ok": False, "message": "Không thể đặt hàng do có sản phẩm đã ngừng kinh doanh: " + "; ".join(discontinued_errors)}

        if stock_errors:
            return {"ok": False, "message": "Không thể đặt hàng do tồn kho không đủ: " + "; ".join(stock_errors)}

        consumed_promotion = None
        consumed_promotion_discount = Decimal("0")
        locked_entered_code = (entered_promotion_code or "").strip().upper()

        if locked_entered_code:
            now = timezone.now()
            locked_promotion = (
                Promotion.objects.select_for_update()
                .filter(
                    code__iexact=locked_entered_code,
                    status=True,
                    start_date__lte=now,
                    end_date__gte=now,
                    used_count__lt=F("usage_limit"),
                )
                .first()
            )

            if not locked_promotion:
                return {
                    "ok": False,
                    "message": f"Mã {locked_entered_code} không hợp lệ, đã hết hạn hoặc đã hết lượt dùng.",
                }

            locked_context = {
                "promotions_by_code": {locked_promotion.code.upper(): locked_promotion},
                "promotion_product_map": {},
            }
            product_scope_ids = set(
                PromotionProduct.objects.filter(id_promotions_id=locked_promotion.id_promotions)
                .values_list("id_products_id", flat=True)
            )
            if product_scope_ids:
                locked_context["promotion_product_map"][locked_promotion.id_promotions] = product_scope_ids

            locked_eval = _evaluate_promotion_code(
                locked_promotion.code,
                cart_items_with_product,
                locked_context,
                user_id=user_id,
            )
            if not locked_eval["is_applied"]:
                return {
                    "ok": False,
                    "message": locked_eval.get("message", "Mã giảm giá không áp dụng được."),
                }

            consumed_promotion = locked_promotion
            consumed_promotion_discount = locked_eval["amount"]

        # Prefer an existing saved address selected by the user during checkout.
        user_address_id = None
        if selected_address_id:
            selected_address = (
                UserAddress.objects.filter(
                    id_user_addresses=selected_address_id,
                    id_users_id=user_id,
                )
                .only("id_user_addresses")
                .first()
            )
            if selected_address:
                user_address_id = selected_address.id_user_addresses

        # Fallback: create/reuse address from submitted phone + address.
        if not user_address_id and address and phone:
            user_address, created = UserAddress.objects.get_or_create(
                id_users_id=user_id,
                full_address=address,
                phone_address=phone,
                defaults={
                    "address_name": "Địa chỉ giao hàng",
                    "is_default": False,
                }
            )
            user_address_id = user_address.id_user_addresses

        order_total = max(Decimal("0"), subtotal_after_product_discount - consumed_promotion_discount)
        order = Order.objects.create(
            id_users_id=user_id,
            id_user_addresses_id=user_address_id,
            total_price_orders=order_total,
            status_orders="pending",
        )

        purchased_product_ids = []
        purchase_behavior_rows = []
        for item in cart_items_with_product:
            OrderItem.objects.create(
                id_orders_id=order.id_orders,
                id_products_id=item["product_id"],
                quantity_order_items=item["quantity"],
                price_order_items=item["product"]["unit_final_price"],
            )
            purchased_product_ids.append(item["product_id"])
            purchase_behavior_rows.append(
                UserBehavior(
                    id_users_id=user_id,
                    id_products_id=item["product_id"],
                    action_type_user_behavior="purchase",
                )
            )

        if purchase_behavior_rows:
            UserBehavior.objects.bulk_create(purchase_behavior_rows)

        for product_id, required_qty in product_quantity_map.items():
            Product.objects.filter(id_products=product_id).update(stock=F("stock") - required_qty)

        if consumed_promotion:
            per_user_limit = int(consumed_promotion.usage_limit_per_user or 1)
            if per_user_limit < 1:
                per_user_limit = 1

            user_promotion = (
                UserPromotion.objects.select_for_update()
                .filter(id_users_id=user_id, id_promotions_id=consumed_promotion.id_promotions)
                .first()
            )
            if not user_promotion:
                try:
                    user_promotion = UserPromotion.objects.create(
                        id_users_id=user_id,
                        id_promotions_id=consumed_promotion.id_promotions,
                        is_saved=False,
                        used_count=0,
                        saved_at=None,
                        last_used_at=None,
                    )
                except IntegrityError:
                    user_promotion = (
                        UserPromotion.objects.select_for_update()
                        .filter(id_users_id=user_id, id_promotions_id=consumed_promotion.id_promotions)
                        .first()
                    )

            if not user_promotion:
                return {"ok": False, "message": "Không thể ghi nhận trạng thái mã giảm giá. Vui lòng thử lại."}

            if int(user_promotion.used_count or 0) >= per_user_limit:
                return {
                    "ok": False,
                    "message": f"Mã {consumed_promotion.code} đã được sử dụng cho tài khoản này.",
                }

            user_promotion.used_count = int(user_promotion.used_count or 0) + 1
            user_promotion.last_used_at = timezone.now()
            if user_promotion.saved_at is None and bool(user_promotion.is_saved):
                user_promotion.saved_at = timezone.now()
            user_promotion.save(update_fields=["used_count", "last_used_at", "saved_at"])

            # Atomically increment promotion used_count but only if there's remaining usage left.
            updated = Promotion.objects.filter(
                id_promotions=consumed_promotion.id_promotions,
                used_count__lt=F("usage_limit"),
            ).update(used_count=F("used_count") + 1)

            if not updated:
                # Another transaction consumed the last usage concurrently.
                return {
                    "ok": False,
                    "message": f"Mã {consumed_promotion.code} đã vừa hết lượt sử dụng. Vui lòng kiểm tra lại.",
                }

            if consumed_promotion.code.upper() in saved_promotion_codes:
                new_saved_codes = [code for code in saved_promotion_codes if code != consumed_promotion.code.upper()]
                _set_saved_promotion_codes(request, new_saved_codes)

        _remove_purchased_items_from_database_cart(user_id, purchased_product_ids)

    return {"ok": True, "order": order}


def _get_hot_sale_products(limit=10):
    """Get products with highest discounts for hot sale section"""
    image_exists_subquery = ProductImage.objects.filter(id_products_id=OuterRef("id_products"))
    products_queryset = (
        _get_public_products_queryset()
        .filter(status="Đang kinh doanh")
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
    
    # Sort by discount amount (highest first), then by percentage
    hot_sale_products.sort(
        key=lambda x: (x.discount_amount, x.discount_percentage),
        reverse=True
    )
    
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
    """Return only products that are not discontinued (active)."""
    if not products:
        return []
    return [p for p in products if not getattr(p, "is_discontinued", False) and _is_active_product(p)]


def _get_popular_products_for_recommendation(limit=8):
    # Popular candidates for recommendations should only include active products
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

    # Home recommendation section looks balanced at 1 row (6) or 2 rows (12).
    if limit >= 12:
        if total_count >= 12:
            return 12
        if total_count >= 6:
            return 6

    return min(total_count, limit)


def _fill_recommendations_to_limit(initial_products, limit=12, exclude_ids=None):
    """Ensure we return up to `limit` products by filling with popular/active products.

    initial_products: list of Product instances already formatted
    """
    if not isinstance(limit, int) or limit <= 0:
        return initial_products

    # Start from only active products (exclude discontinued from recommendation lists)
    products = [p for p in (initial_products or []) if not getattr(p, "is_discontinued", False) and getattr(p, "status", "") == "Đang kinh doanh"]
    exclude = set(exclude_ids or [])
    existing_ids = {getattr(p, "id_products", None) for p in products if getattr(p, "id_products", None) is not None}
    exclude.update(existing_ids)

    if len(products) >= limit:
        return products[:limit]

    # First try popular products (already excludes discontinued via public queryset)
    popular_candidates = _get_popular_products_for_recommendation(limit=limit * 3)
    for p in popular_candidates:
        if getattr(p, "id_products", None) in exclude:
            continue
        products.append(p)
        exclude.add(p.id_products)
        if len(products) >= limit:
            break

    # As a last resort, pull newest active products from public queryset
    if len(products) < limit:
        # Pull newest active products as a last resort
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
            # exclude discontinued from recommendations
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
            # Newer/stronger session signals get a slightly higher rank score.
            session_scores[int(product_id)] = float(total_session_ids - index) / float(total_session_ids)

    if not session_scores and not user_scores and not item_scores:
        popular_products = _get_popular_products_for_recommendation(limit=limit)
        filled = _fill_recommendations_to_limit(popular_products, limit=limit)
        return filled, "popular"

    def _normalize_scores(score_map):
        max_score = max(score_map.values(), default=0.0)
        if max_score <= 0:
            return {}
        return {
            int(product_id): float(score) / max_score
            for product_id, score in score_map.items()
        }

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
    normalized_components = [
        (name, score_map, weight / total_weight)
        for name, score_map, weight in active_components
    ]

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
        for product_id, _ in sorted(
            combined_scores.items(),
            key=lambda pair: (pair[1], pair[0]),
            reverse=True,
        )
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
    # Ensure we return up to `limit` products (fill with popular/active products if needed)
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
    has_product_filters = bool(
        selected_category_id or search_query or selected_brand or selected_price_range or selected_best_selling
    )

    image_exists_subquery = ProductImage.objects.filter(id_products_id=OuterRef("id_products"))
    products_queryset = _get_public_products_queryset().annotate(has_image=Exists(image_exists_subquery))

    selected_category_name = ""
    if selected_category_id.isdigit():
        products_queryset = products_queryset.filter(id_categories_id=int(selected_category_id))
        selected_category = _get_public_categories_queryset().filter(id_categories=int(selected_category_id)).first()
        if selected_category:
            selected_category_name = selected_category.name_categories

    # Lọc sản phẩm theo khoảng giá và category nếu có
    products_queryset = filter_products_by_price(request, base_queryset=products_queryset)

    # Nếu muốn kết hợp thêm các filter khác (brand, search_query) thì tiếp tục filter trên products_queryset
    # Không dùng order_by("-has_image", ...) vì has_image không phải là field thực tế
    products_queryset = _apply_search_filters(
        products_queryset,
        search_query,
        selected_brand,
        selected_price_range,
    )

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
    
    # Add hot sale products
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

            existing_review = (
                Review.objects.filter(id_users_id=user_id, id_products_id=product_id)
                .order_by("-id_reviews")
                .first()
            )
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
        product_obj = (
            _get_public_products_queryset()
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

    review_summary = _get_product_review_summary(product_obj.id_products)
    visible_reviews = _get_visible_product_reviews(product_obj.id_products, limit=25)

    can_review = False
    my_review = None
    if user_id:
        can_review = _user_has_completed_purchase(user_id, product_obj.id_products)
        my_review = (
            Review.objects.filter(id_users_id=user_id, id_products_id=product_obj.id_products)
            .order_by("-id_reviews")
            .first()
        )

    pricing = _get_product_pricing(product_obj)
    
    # Determine stock status
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
    recommended_products = [
        item for item in recommended_products
        if int(getattr(item, "id_products", 0) or 0) != int(product_id)
    ][:6]
    
    # Parse description to table format
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
            "promotions": [
                _format_promotion_text(item)
                for item in promotion_context["promotions_by_code"].values()
            ],
        }
    )
    return render(request, "store/pages/product_detail.html", context)


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
            for item in _get_public_products_queryset()
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
        products_map = {
            item.id_products: item
            for item in _get_public_products_queryset()
            .filter(id_products__in=ordered_product_ids)
        }
        products = [products_map[product_id] for product_id in ordered_product_ids if product_id in products_map]

    completed_product_ids = set(
        OrderItem.objects.filter(
            id_orders__id_users_id=user_id,
            id_orders__status_orders="completed",
            id_products_id__in=ordered_product_ids,
        ).values_list("id_products_id", flat=True)
    )
    reviewed_product_ids = set(
        Review.objects.filter(id_users_id=user_id, id_products_id__in=ordered_product_ids)
        .values_list("id_products_id", flat=True)
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

    grouped_categories = sorted(
        category_group_map.values(),
        key=lambda item: (item["category_name"] or "").lower(),
    )

    context = _common_page_context(request)
    context.update(
        {
            "grouped_categories": grouped_categories,
            "purchased_product_count": len(products),
            "pending_review_count": sum(
                1
                for item in products
                if getattr(item, "can_review", False) and not getattr(item, "has_reviewed", False)
            ),
        }
    )

    return render(request, "store/pages/purchased_products.html", context)


def _safe_redirect_back_home(default_auth_tab="login"):
    return redirect(f"/?auth={default_auth_tab}")


def _clear_login_session(request):
    for key in (
        "cart_items",
        "logged_in_user_id",
        "logged_in_user_name",
        "logged_in_user_role",
    ):
        request.session.pop(key, None)


def _enforce_active_user(request, json_response=False):
    user_id = request.session.get("logged_in_user_id")
    if not user_id:
        return None

    status_value = (
        User.objects.filter(id_users=user_id)
        .values_list("status", flat=True)
        .first()
    )
    if status_value is None:
        return None

    try:
        status_value = int(status_value)
    except (TypeError, ValueError):
        status_value = 1

    if status_value == 0:
        _clear_login_session(request)
        msg = "Tài khoản của bạn đã bị khóa hoặc ngừng hoạt động."
        if json_response:
            return JsonResponse({"success": False, "message": msg}, status=403)
        messages.error(request, msg)
        return redirect("/?auth=login")

    return None


def _enforce_password_change(request, allow_password_change=False, json_response=False):
    return None


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

    try:
        user = User.objects.create(
            name_users=name,
            email=email,
            password=make_password(password),
            role="user",
            gender_users=gender or None,
            phone_users=phone or None,
            address_users=address or None,
        )
    except IntegrityError:
        messages.error(request, "Không thể tạo tài khoản do cấu hình dữ liệu không hợp lệ. Vui lòng thử lại.")
        request.session["register_old"] = {
            "name_users": name,
            "email": email,
            "gender_users": gender,
            "phone_users": phone,
            "address_users": address,
        }
        return _safe_redirect_back_home("register")

    request.session["logged_in_user_id"] = user.id_users
    request.session["logged_in_user_name"] = user.name_users
    request.session["logged_in_user_role"] = (user.role or "user").strip().lower()
    request.session.pop("cart_items", None)

    messages.success(request, "Đăng ký thành công")
    return redirect("/")


@require_POST
def login_user(request):
    email = (request.POST.get("email") or "").strip().lower()
    password = request.POST.get("password") or ""

    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"

    if not email or not password:
        msg = "Email và mật khẩu là bắt buộc."
        if is_ajax:
            return JsonResponse({"success": False, "message": msg}, status=400)
        else:
            messages.error(request, msg)
            return _safe_redirect_back_home("login")

    user = User.objects.filter(email=email).first()
    if not user:
        msg = "Tài khoản hoặc mật khẩu không đúng."
        if is_ajax:
            return JsonResponse({"success": False, "message": msg}, status=400)
        else:
            messages.error(request, msg)
            return _safe_redirect_back_home("login")

    hashed_ok = check_password(password, user.password)
    plain_ok = user.password == password

    if not (hashed_ok or plain_ok):
        msg = "Tài khoản hoặc mật khẩu không đúng."
        if is_ajax:
            return JsonResponse({"success": False, "message": msg}, status=400)
        else:
            messages.error(request, msg)
            return _safe_redirect_back_home("login")

    status_value = getattr(user, "status", 1)
    try:
        status_value = int(status_value)
    except (TypeError, ValueError):
        status_value = 1

    if status_value == 0:
        msg = "Tài khoản đã bị khóa hoặc không còn hoạt động."
        if is_ajax:
            return JsonResponse({"success": False, "message": msg}, status=403)
        else:
            messages.error(request, msg)
            return _safe_redirect_back_home("login")

    if plain_ok:
        user.password = make_password(password)
        user.save(update_fields=["password"])

    request.session["logged_in_user_id"] = user.id_users
    request.session["logged_in_user_name"] = user.name_users
    request.session["logged_in_user_role"] = (user.role or "").strip().lower()
    request.session.pop("cart_items", None)

    if is_ajax:
        return JsonResponse({"success": True, "message": "Đăng nhập thành công."})
    else:
        return redirect("/")


@require_POST
def logout_user(request):
    request.session.pop("cart_items", None)
    request.session.pop("logged_in_user_id", None)
    request.session.pop("logged_in_user_name", None)
    request.session.pop("logged_in_user_role", None)
    messages.success(request, "Đã đăng xuất")
    return redirect("/")


def account_info(request):
    """Xem và chỉnh sửa thông tin tài khoản"""
    user_id = request.session.get("logged_in_user_id")
    if not user_id:
        messages.error(request, "Vui lòng đăng nhập để xem tài khoản")
        return redirect("/?auth=login")

    locked_response = _enforce_active_user(request)
    if locked_response:
        return locked_response

    try:
        user = User.objects.get(id_users=user_id)
    except User.DoesNotExist:
        request.session.pop("logged_in_user_id", None)
        request.session.pop("logged_in_user_name", None)
        messages.error(request, "Tài khoản không tồn tại")
        return redirect("/")

    categories = list(_get_public_categories_queryset().order_by("name_categories")[:10])
    
    # Thêm brand_list cho mỗi category (nếu cần)
    for cat in categories:
        cat.brand_list = []

    user_addresses = list(
        UserAddress.objects.filter(id_users_id=user_id).order_by("-is_default", "-created_at_addresses", "-id_user_addresses")
    )

    context = {
        "user": user,
        "user_addresses": user_addresses,
        "categories": categories,
        "logged_in_user_id": user_id,
        "logged_in_user_name": request.session.get("logged_in_user_name"),
        "clear_cart_client": False,
    }

    if request.method == "POST":
        action = (request.POST.get("action") or "update_profile").strip().lower()

        if action == "change_password":
            current_password = request.POST.get("current_password") or ""
            new_password = request.POST.get("new_password") or ""
            confirm_password = request.POST.get("confirm_password") or ""

            if not current_password or not new_password or not confirm_password:
                messages.error(request, "Vui lòng nhập đầy đủ thông tin mật khẩu")
                return redirect("account_info")

            hashed_ok = check_password(current_password, user.password)
            plain_ok = user.password == current_password

            if not (hashed_ok or plain_ok):
                messages.error(request, "Mật khẩu hiện tại không đúng")
                return redirect("account_info")

            if new_password != confirm_password:
                messages.error(request, "Mật khẩu mới không khớp")
                return redirect("account_info")

            if len(new_password) < 6:
                messages.error(request, "Mật khẩu mới phải có ít nhất 6 ký tự")
                return redirect("account_info")

            if new_password == current_password:
                messages.error(request, "Mật khẩu mới phải khác mật khẩu hiện tại")
                return redirect("account_info")

            user.password = make_password(new_password)
            user.save(update_fields=["password"])
            messages.success(request, "Đổi mật khẩu thành công")
            return redirect("account_info")

        if action == "add_address":
            address_name = (request.POST.get("address_name") or "").strip()
            full_address = (request.POST.get("full_address") or "").strip()
            phone_address = (request.POST.get("phone_address") or "").strip()
            set_default = (request.POST.get("is_default") or "") == "1"

            allowed_address_names = {"Nhà riêng", "Công ty", "Khác"}
            if address_name not in allowed_address_names:
                address_name = "Khác"

            if not full_address:
                messages.error(request, "Vui lòng nhập địa chỉ giao hàng")
                return redirect("account_info")

            if not phone_address:
                phone_address = (user.phone_users or "").strip()

            with transaction.atomic():
                is_default = set_default
                if is_default:
                    UserAddress.objects.filter(id_users_id=user_id, is_default=True).update(is_default=False)

                created_address = UserAddress.objects.create(
                    id_users_id=user_id,
                    address_name=address_name,
                    full_address=full_address,
                    phone_address=phone_address or None,
                    is_default=is_default,
                )

                if created_address.is_default:
                    user.address_users = created_address.full_address
                    if created_address.phone_address:
                        user.phone_users = created_address.phone_address
                    user.save(update_fields=["address_users", "phone_users"])

            messages.success(request, "Đã thêm địa chỉ giao hàng")
            return redirect("account_info")

        if action == "edit_address":
            address_id = (request.POST.get("address_id") or "").strip()
            if not address_id.isdigit():
                messages.error(request, "Địa chỉ không hợp lệ")
                return redirect("account_info")

            address_name = (request.POST.get("address_name") or "").strip()
            full_address = (request.POST.get("full_address") or "").strip()
            phone_address = (request.POST.get("phone_address") or "").strip()
            set_default = (request.POST.get("is_default") or "") == "1"

            allowed_address_names = {"Nhà riêng", "Công ty", "Khác"}
            if address_name not in allowed_address_names:
                address_name = "Khác"

            if not full_address:
                messages.error(request, "Vui lòng nhập địa chỉ giao hàng")
                return redirect("account_info")

            with transaction.atomic():
                addr = UserAddress.objects.filter(id_user_addresses=int(address_id), id_users_id=user_id).first()
                if not addr:
                    messages.error(request, "Không tìm thấy địa chỉ")
                    return redirect("account_info")

                # Update fields
                addr.address_name = address_name
                addr.full_address = full_address
                addr.phone_address = phone_address or None

                if set_default and not addr.is_default:
                    UserAddress.objects.filter(id_users_id=user_id, is_default=True).update(is_default=False)
                    addr.is_default = True

                addr.save(update_fields=[f for f in ["address_name", "full_address", "phone_address", "is_default"] if hasattr(addr, f)])

                # If this becomes default, update user's profile address/phone
                if addr.is_default:
                    user.address_users = addr.full_address
                    if addr.phone_address:
                        user.phone_users = addr.phone_address
                    user.save(update_fields=["address_users", "phone_users"])

            messages.success(request, "Đã cập nhật địa chỉ giao hàng")
            return redirect("account_info")

        if action == "delete_address":
            address_id = (request.POST.get("address_id") or "").strip()
            if not address_id.isdigit():
                messages.error(request, "Địa chỉ không hợp lệ")
                return redirect("account_info")

            with transaction.atomic():
                address_obj = UserAddress.objects.filter(
                    id_user_addresses=int(address_id),
                    id_users_id=user_id,
                ).first()
                if not address_obj:
                    messages.error(request, "Không tìm thấy địa chỉ")
                    return redirect("account_info")

                # Nếu địa chỉ đang được tham chiếu bởi các đơn hàng thì không xóa được
                referenced_by_orders = Order.objects.filter(id_user_addresses_id=address_obj.id_user_addresses).exists()
                if referenced_by_orders:
                    messages.error(
                        request,
                        "Không thể xóa địa chỉ này vì đã được sử dụng trong đơn hàng. Vui lòng chỉnh sửa thông tin nếu cần hoặc liên hệ hỗ trợ.",
                    )
                    return redirect("account_info")

                was_default = bool(address_obj.is_default)
                address_obj.delete()

                if was_default:
                    replacement = UserAddress.objects.filter(id_users_id=user_id).order_by("-created_at_addresses", "-id_user_addresses").first()
                    if replacement:
                        replacement.is_default = True
                        replacement.save(update_fields=["is_default"])

            messages.success(request, "Đã xóa địa chỉ")
            return redirect("account_info")

        if action == "set_default_address":
            address_id = (request.POST.get("address_id") or "").strip()
            if not address_id.isdigit():
                messages.error(request, "Địa chỉ không hợp lệ")
                return redirect("account_info")

            with transaction.atomic():
                address_obj = UserAddress.objects.filter(
                    id_user_addresses=int(address_id),
                    id_users_id=user_id,
                ).first()
                if not address_obj:
                    messages.error(request, "Không tìm thấy địa chỉ")
                    return redirect("account_info")

                UserAddress.objects.filter(id_users_id=user_id, is_default=True).exclude(
                    id_user_addresses=address_obj.id_user_addresses
                ).update(is_default=False)

                if not address_obj.is_default:
                    address_obj.is_default = True
                    address_obj.save(update_fields=["is_default"])

                user.address_users = address_obj.full_address
                if address_obj.phone_address:
                    user.phone_users = address_obj.phone_address
                user.save(update_fields=["address_users", "phone_users"])

            messages.success(request, "Đã cập nhật địa chỉ mặc định")
            return redirect("account_info")

        name = (request.POST.get("name_users") or "").strip()
        email = (request.POST.get("email") or "").strip().lower()
        gender = (request.POST.get("gender_users") or "").strip()
        phone = (request.POST.get("phone_users") or "").strip()
        address = (request.POST.get("address_users") or "").strip()

        if not name:
            messages.error(request, "Tên không được để trống")
            return render(request, "store/pages/account.html", context)

        if not email:
            messages.error(request, "Email không được để trống")
            return render(request, "store/pages/account.html", context)

        if User.objects.filter(email=email).exclude(id_users=user.id_users).exists():
            messages.error(request, "Email đã được sử dụng bởi tài khoản khác")
            return render(request, "store/pages/account.html", context)

        user.name_users = name
        user.email = email
        user.gender_users = gender or None
        user.phone_users = phone or None
        user.address_users = address or None
        user.save()

        # Đồng bộ địa chỉ mặc định theo hồ sơ khách hàng sau khi cập nhật profile.
        if address:
            with transaction.atomic():
                UserAddress.objects.filter(id_users_id=user_id, is_default=True).update(is_default=False)
                profile_address, _created = UserAddress.objects.get_or_create(
                    id_users_id=user_id,
                    full_address=address,
                    defaults={
                        "address_name": "Địa chỉ hồ sơ",
                        "phone_address": phone or None,
                        "is_default": True,
                    },
                )
                if not profile_address.is_default:
                    profile_address.is_default = True
                if phone and profile_address.phone_address != phone:
                    profile_address.phone_address = phone
                profile_address.save(update_fields=["is_default", "phone_address"])

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
        "status": product.status,
        "is_discontinued": product.is_discontinued,
    }


@require_GET
def get_products(request):

    products = _get_public_products_queryset().all()

    data = [serialize_product(product) for product in products]

    return JsonResponse(data, safe=False)


@require_GET
def get_product_detail(request, id):

    try:
        product = _get_public_products_queryset().get(id_products=id)
    except Product.DoesNotExist:
        return JsonResponse({"error": "Product not found"}, status=404)

    # Đã xóa ProductDetail, chỉ trả về thông tin sản phẩm cơ bản
    data = serialize_product(product)
    return JsonResponse(data)


@require_GET
def get_categories(request):

    categories = _get_public_categories_queryset()

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

    products = _get_public_products_queryset()

    if selected_category_id.isdigit():
        products = products.filter(id_categories_id=int(selected_category_id))

    products = _apply_search_filters(products, keyword, selected_brand, selected_price_range)

    if not keyword:
        products = products[:50]

    _save_search_history_for_logged_in_user(request, keyword)

    data = [serialize_product(product) for product in products]

    return JsonResponse(data, safe=False)


@require_GET
def search_autocomplete(request):
    keyword = (request.GET.get("q") or "").strip()
    if len(keyword) < 2:
        return JsonResponse({"items": []})

    try:
        limit = int(request.GET.get("limit") or 8)
    except (TypeError, ValueError):
        limit = 8

    limit = max(1, min(limit, 12))
    keyword_lower = keyword.lower()
    tokens = [token for token in keyword_lower.split() if token]

    candidate_products = list(
        _get_public_products_queryset()
        .annotate(total_sold=Coalesce(Sum("orderitem__quantity_order_items"), 0))
        .filter(
            Q(name_products__icontains=keyword)
            | Q(brand__icontains=keyword)
            | Q(id_categories__name_categories__icontains=keyword)
        )
        .order_by("-total_sold", "-id_products")[:60]
    )

    scored_candidates = []
    for product in candidate_products:
        name_value = (product.name_products or "").strip()
        name_lower = name_value.lower()
        brand_value = (product.brand or "").strip()
        brand_lower = brand_value.lower()
        category_name = product.id_categories.name_categories if product.id_categories else ""
        category_lower = category_name.lower() if category_name else ""

        relevance = 0.0
        if name_lower == keyword_lower:
            relevance += 120.0
        if name_lower.startswith(keyword_lower):
            relevance += 65.0
        if keyword_lower in name_lower:
            relevance += 35.0

        if brand_lower.startswith(keyword_lower):
            relevance += 24.0
        elif keyword_lower in brand_lower:
            relevance += 14.0

        if category_lower.startswith(keyword_lower):
            relevance += 14.0
        elif keyword_lower in category_lower:
            relevance += 8.0

        if tokens and all(token in name_lower for token in tokens):
            relevance += 22.0
        elif tokens and any(token in name_lower for token in tokens):
            relevance += 10.0

        popularity = min(float(getattr(product, "total_sold", 0) or 0), 5000.0) / 350.0
        total_score = relevance + popularity

        scored_candidates.append((total_score, int(getattr(product, "total_sold", 0) or 0), product))

    scored_candidates.sort(key=lambda row: (row[0], row[1], row[2].id_products), reverse=True)

    items = []
    for _, _, product in scored_candidates[:limit]:
        image_url = _pick_primary_image(product) or ""
        items.append(
            {
                "id": product.id_products,
                "name": product.name_products,
                "brand": product.brand or "",
                "category": product.id_categories.name_categories if product.id_categories else "",
                "image": image_url,
                "url": f"/products/{product.id_products}/",
            }
        )

    return JsonResponse({"items": items})


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

    normalized_action = BEHAVIOR_ACTION_ALIASES.get(body["action"], body["action"])
    if normalized_action not in BEHAVIOR_ALLOWED_ACTIONS:
        return JsonResponse(
            {"error": f"Invalid action '{body['action']}'"},
            status=400,
        )

    _append_session_behavior_event(request, product_id, normalized_action)

    if not user_id:
        return JsonResponse({"status": "ok", "scope": "session"})

    UserBehavior.objects.create(
        id_users_id=user_id,
        id_products_id=product_id,
        action_type_user_behavior=normalized_action,
    )

    return JsonResponse({"status": "ok", "scope": "user"})


@require_GET
def session_recommendations(request):
    limit_value = request.GET.get("limit") or "8"
    try:
        limit = max(1, min(int(limit_value), 24))
    except (TypeError, ValueError):
        limit = 8

    recommended_products, recommendation_source = _get_personalized_products_for_home(request, limit=limit)
    rendered_cards = []
    for product in recommended_products:
        rendered_cards.append(
            render_to_string(
                "store/components/product_card.html",
                {"product": product},
                request=request,
            )
        )

    return JsonResponse(
        {
            "source": recommendation_source,
            "items_html": "".join(rendered_cards),
            "count": len(recommended_products),
        }
    )


@require_GET
def session_recommendations_more(request):
    try:
        limit = max(1, min(int(request.GET.get("limit") or 12), 24))
    except (TypeError, ValueError):
        limit = 12

    try:
        offset = max(0, int(request.GET.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0

    fetch_limit = offset + limit
    recommended_products, recommendation_source = _get_personalized_products_for_home(request, limit=fetch_limit)
    sliced = recommended_products[offset:offset + limit]

    rendered_cards = [
        render_to_string("store/components/product_card.html", {"product": product}, request=request)
        for product in sliced
    ]

    has_more = len(recommended_products) > (offset + limit)
    return JsonResponse(
        {
            "source": recommendation_source,
            "items_html": "".join(rendered_cards),
            "count": len(sliced),
            "has_more": has_more,
        }
    )


@require_GET
def best_sellers_api(request):
    try:
        limit = max(1, min(int(request.GET.get("limit") or 12), 24))
    except (TypeError, ValueError):
        limit = 12

    try:
        offset = max(0, int(request.GET.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0

    try:
        window_days = max(1, min(int(request.GET.get("window") or 30), 90))
    except (TypeError, ValueError):
        window_days = 30

    products = _get_best_selling_products(limit=limit + 1, offset=offset, window_days=window_days)
    has_more = len(products) > limit
    if has_more:
        products = products[:limit]

    rendered_cards = [
        render_to_string("store/components/product_card.html", {"product": product}, request=request)
        for product in products
    ]
    return JsonResponse(
        {
            "items_html": "".join(rendered_cards),
            "count": len(products),
            "has_more": has_more,
        }
    )


@csrf_exempt
@require_POST
def ai_data_chat(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    question = str(body.get("question") or "").strip()
    if not question:
        return JsonResponse({"error": "Missing required field: question"}, status=400)


    # Lấy Gemini API key
    api_key = os.environ.get("GEMINI_API_KEY") or str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if not api_key:
        return JsonResponse({"error": "GEMINI_API_KEY is not configured"}, status=503)

    context = _build_ai_chat_data_context(question, limit=60)
    budget_text = "khong xac dinh"
    if context.get("budget"):
        budget_text = f"{int(context['budget']):,} VND"

    system_prompt = (
        "Bạn là trợ lý chăm sóc khách hàng của cửa hàng PC AI. "
        "Hãy trả lời bằng tiếng Việt tự nhiên, ngắn gọn, hữu ích và chỉ dựa trên dữ liệu sản phẩm/danh mục/tồn kho được cung cấp. "
        "Bạn có thể trả lời các câu hỏi về sản phẩm, giá, tồn kho, danh mục, gợi ý mua hàng, hoặc build PC. "
        "Không được bịa thêm thông tin ngoài dữ liệu, nhưng cũng không từ chối sớm: nếu thiếu dữ liệu cho một phần nào đó, hãy nói rõ phần còn thiếu và gợi ý lựa chọn gần nhất còn hàng. "
        "Nếu khách yêu cầu build PC, hãy ưu tiên đề xuất cấu hình tối ưu theo nhu cầu, và khi chắc chắn thì có thể kèm JSON cấu hình gợi ý. "
        "Nếu câu hỏi chỉ là hỏi thông tin sản phẩm, hãy trả lời trực tiếp, không bắt buộc bảng JSON."
    )

    user_prompt = (
        f"Câu hỏi của khách: {question}\n"
        f"Mục đích sử dụng: {context['intent']}\n"
        f"Ngân sách dự kiến: {budget_text}\n"
        f"Danh mục linh kiện hiện có: {context['categories_text']}\n"
        f"Số sản phẩm trong dữ liệu: {context['products_count']} / {context['catalog_count']}\n"
        "Dữ liệu sản phẩm chi tiết:\n"
        f"{context['products_text']}\n"
        "Hướng dẫn: nếu khách hỏi thông tin sản phẩm thông thường thì trả lời tự nhiên, không cần JSON. "
        "Chỉ khi khách hỏi build PC hoặc muốn cấu hình thì mới trả về gợi ý cấu hình ngắn gọn."
    )

    payload = {
        "contents": [
            {"role": "user", "parts": [{"text": system_prompt + "\n" + user_prompt}]}
        ]
    }
    raw_models = os.environ.get("GEMINI_MODEL") or str(getattr(settings, "GEMINI_MODEL", "") or "").strip()
    if raw_models:
        models = [model.strip() for model in re.split(r"[\s,]+", raw_models) if model.strip()]
    else:
        models = [
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-2.0-flash",
            "gemini-2.0-flash-001",
            "gemini-2.0-flash-lite-001",
            "gemini-2.0-flash-lite",
            "gemini-2.5-flash-lite",
        ]

    parsed = None
    last_error = None

    for model in models:
        endpoint = f"{GEMINI_CHAT_ENDPOINT.format(model=model)}?key={api_key}"
        try:
            resp = requests.post(
                endpoint,
                json=payload,
                timeout=35
            )
        except Exception as e:
            last_error = {"error": "Gemini request failed", "details": str(e)}
            continue

        if resp.status_code == 404:
            last_error = {"error": "Gemini request failed", "details": resp.text, "model": model}
            continue

        if resp.status_code != 200:
            return JsonResponse({"error": "Gemini request failed", "details": resp.text}, status=502)

        try:
            parsed = resp.json()
        except Exception as e:
            return JsonResponse({"error": "Gemini request failed", "details": str(e)}, status=502)

        if parsed:
            break

    if not parsed:
        available_models, list_error = _list_gemini_models(api_key)
        error_payload = last_error or {"error": "Gemini request failed", "details": "No available model."}
        if available_models:
            error_payload["available_models"] = available_models
        elif list_error:
            error_payload["available_models_error"] = list_error
        return JsonResponse(error_payload, status=502)

    # Gemini response: { candidates: [ { content: { parts: [ { text: ... } ] } } ] }
    candidates = parsed.get("candidates")
    if not candidates or not isinstance(candidates, list):
        return JsonResponse({"error": "Invalid response from Gemini", "details": str(parsed)}, status=502)
    content = candidates[0].get("content", {})
    parts = content.get("parts", [])
    answer = ""
    if parts and isinstance(parts, list):
        answer = str(parts[0].get("text") or "").strip()
    if not answer:
        return JsonResponse({"error": "Empty answer from Gemini"}, status=502)

    # Trích xuất JSON cấu hình gợi ý từ câu trả lời (nếu có)
    config_json = None
    json_match = re.search(r'\{[\s\S]+?\}', answer)
    if json_match:
        try:
            config_json = json.loads(json_match.group(0))
        except Exception:
            config_json = None

    return JsonResponse({"answer": answer, "suggested_config": config_json})


@require_POST
def save_cart_to_session(request):
    """Lưu giỏ hàng từ frontend vào session backend"""
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
        
        # Lưu vào session
        request.session["cart_items"] = cart_items
        request.session.modified = True

        # Đồng bộ vào database để hiển thị trong Admin (Cart / CartItem)
        if sync_db:
            _sync_cart_to_database(user_id, cart_items)
        
        return JsonResponse({"status": "ok"})
    except (json.JSONDecodeError, KeyError) as e:
        return JsonResponse({"error": str(e)}, status=400)


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


def checkout(request):
    """Trang checkout - xác nhận đơn hàng"""
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

    # Lấy cart items từ session
    cart_items_session = request.session.get("cart_items", [])
    
    # Nếu không có cart items, return error
    if not cart_items_session:
        messages.warning(request, "Giỏ hàng trống")
        return redirect("/cart/")

    promotion_context = _build_promotion_context()

    # Tính tổng tiền theo 2 tầng: discount sản phẩm -> promotion code
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
        selected_address_obj = next(
            (addr for addr in user_addresses if addr.id_user_addresses == int(selected_address_id)),
            None,
        )

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
    context.update({
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
    })

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
            selected_checkout_address = next(
                (addr for addr in user_addresses if addr.id_user_addresses == int(posted_selected_address_id)),
                None,
            )

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


def order_list(request):
    """Danh sách đơn hàng của user"""
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


@require_http_methods(["GET", "POST"])
def order_detail(request, order_id):
    """Chi tiết đơn hàng"""
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

            # Hoàn lại số lượng sản phẩm vào kho
            order_items = OrderItem.objects.filter(id_orders_id=order_id)
            for item in order_items:
                product = item.id_products
                if product:
                    product.stock = (product.stock or 0) + item.quantity_order_items
                    product.save(update_fields=["stock"])

        # Lưu lý do hủy vào session để hiển thị cho người dùng mà không cần thay đổi schema DB hiện tại.
        cancel_reason_map = request.session.get("order_cancel_reason_map", {})
        cancel_reason_map[str(order_id)] = selected_reason_text
        request.session["order_cancel_reason_map"] = cancel_reason_map

        messages.success(request, f"Đã hủy đơn hàng #{order.id_orders}. Lý do: {selected_reason_text}")
        return redirect("order_detail", order_id=order_id)

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

    context.update({
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
    })

    return render(request, "store/pages/order_detail.html", context)

def filter_products_by_price(request, base_queryset=None):
    price_range = (request.GET.get("price_range") or "").strip()
    category_id = (request.GET.get("category") or "").strip()
    products = base_queryset or _get_public_products_queryset()

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

