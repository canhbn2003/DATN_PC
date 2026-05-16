"""Account and authentication views."""

import random

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.hashers import check_password, make_password
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone
from datetime import timedelta

from .models import Order, User, UserAddress
from .views_utils_shared import _get_public_categories_queryset


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


def _apply_lockout_policy(request, failed_count: int):
    """Apply session-based lockout durations based on failed attempt count.

    - >=3 failures: 1 minute
    - >=5 failures: 5 minutes
    - >=10 failures: 60 minutes
    """
    try:
        failed = int(failed_count)
    except Exception:
        return

    minutes = None
    if failed >= 10:
        minutes = 60
    elif failed >= 5:
        minutes = 5
    elif failed >= 3:
        minutes = 1

    if minutes:
        locked_until = timezone.now() + timedelta(minutes=minutes)
        # store as ISO string
        request.session["login_locked_until"] = locked_until.isoformat()


def _get_login_locked_until_ts(request):
    """Return locked_until as milliseconds since epoch or None."""
    locked_iso = request.session.get("login_locked_until")
    if not locked_iso:
        return None
    try:
        locked_dt = timezone.datetime.fromisoformat(locked_iso)
        if timezone.is_naive(locked_dt):
            locked_dt = timezone.make_aware(locked_dt, timezone.get_current_timezone())
        return int(locked_dt.timestamp() * 1000)
    except Exception:
        return None


def _lockout_response_if_active(request, is_ajax=False):
    """If session lockout is active, return a JsonResponse (for AJAX) or redirect response.

    Returns: None if not locked, otherwise a Django HttpResponse to return from the view.
    """
    locked_iso = request.session.get("login_locked_until")
    if not locked_iso:
        return None

    try:
        locked_dt = timezone.datetime.fromisoformat(locked_iso)
        if timezone.is_naive(locked_dt):
            locked_dt = timezone.make_aware(locked_dt, timezone.get_current_timezone())
    except Exception:
        return None

    if locked_dt and locked_dt > timezone.now():
        remaining = locked_dt - timezone.now()
        minutes = int(remaining.total_seconds() // 60)
        seconds = int(remaining.total_seconds() % 60)
        human = f"{minutes} phút {seconds} giây" if minutes else f"{seconds} giây"
        msg = f"Đã khóa đăng nhập tạm thời. Vui lòng thử lại sau {human}."
        if is_ajax:
            return JsonResponse({"success": False, "message": msg, "login_locked_until_ts": _get_login_locked_until_ts(request)}, status=429)
        messages.error(request, msg)
        return _safe_redirect_back_home("login")

    return None


@require_POST
@csrf_exempt
def forgot_password(request):
    email = request.POST.get("email")
    if not email:
        return JsonResponse({"success": False, "message": "Vui lòng nhập email."}, status=400)

    try:
        User.objects.get(email=email)
    except User.DoesNotExist:
        return JsonResponse({"success": False, "message": "Email không tồn tại. Vui lòng kiểm tra lại."}, status=200)

    otp = str(random.randint(100000, 999999))
    request.session["reset_password_otp"] = otp
    request.session["reset_password_email"] = email
    request.session.set_expiry(300)

    subject = "Mã xác nhận đặt lại mật khẩu LTC Computer"
    message = f"Mã xác nhận đặt lại mật khẩu của bạn là: {otp}\nMã có hiệu lực trong 5 phút."
    from_email = settings.DEFAULT_FROM_EMAIL if hasattr(settings, "DEFAULT_FROM_EMAIL") else None
    try:
        send_mail(subject, message, from_email, [email], fail_silently=False)
    except Exception as exc:
        return JsonResponse({"success": False, "message": f"Lỗi gửi email: {str(exc)}"}, status=500)

    return JsonResponse({"success": True, "message": "Đã gửi mã xác nhận về email của bạn."})


@require_POST
@csrf_exempt
def verify_otp(request):
    otp_input = request.POST.get("otp")
    otp_session = request.session.get("reset_password_otp")
    email_session = request.session.get("reset_password_email")
    if not otp_input or not otp_session or not email_session:
        return JsonResponse({"success": False, "message": "Thông tin xác thực không hợp lệ."}, status=400)
    if otp_input != otp_session:
        return JsonResponse({"success": False, "message": "Mã xác nhận không đúng hoặc đã hết hạn."}, status=200)
    request.session["otp_verified"] = True
    return JsonResponse({"success": True, "message": "Xác thực mã OTP thành công."})


@require_POST
@csrf_exempt
def reset_password(request):
    if not request.session.get("otp_verified"):
        return JsonResponse({"success": False, "message": "Bạn chưa xác thực mã OTP."}, status=400)
    email = request.session.get("reset_password_email")
    new_password = request.POST.get("new_password")
    confirm_password = request.POST.get("confirm_password")
    if not new_password or not confirm_password:
        return JsonResponse({"success": False, "message": "Vui lòng nhập đầy đủ mật khẩu."}, status=400)
    if new_password != confirm_password:
        return JsonResponse({"success": False, "message": "Mật khẩu nhập lại không khớp."}, status=400)
    if len(new_password) < 6:
        return JsonResponse({"success": False, "message": "Mật khẩu phải có ít nhất 6 ký tự."}, status=400)
    try:
        user = User.objects.get(email=email)
        user.password = make_password(new_password)
        user.save(update_fields=["password"])
        for key in ["reset_password_otp", "reset_password_email", "otp_verified"]:
            if key in request.session:
                del request.session[key]
        return JsonResponse({"success": True, "message": "Đổi mật khẩu thành công. Bạn có thể đăng nhập bằng mật khẩu mới."})
    except User.DoesNotExist:
        return JsonResponse({"success": False, "message": "Tài khoản không tồn tại."}, status=400)


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

    # check session-level lockout (centralized helper)
    lock_resp = _lockout_response_if_active(request, is_ajax=is_ajax)
    if lock_resp:
        return lock_resp

    if not email or not password:
        msg = "Email và mật khẩu là bắt buộc."
        if is_ajax:
            return JsonResponse({"success": False, "message": msg, "login_locked_until_ts": _get_login_locked_until_ts(request)}, status=400)
        messages.error(request, msg)
        return _safe_redirect_back_home("login")

    user = User.objects.filter(email=email).first()
    if not user:
        # increment failed attempts for unknown user (per-session)
        failed = int(request.session.get("login_failed_count", 0)) + 1
        request.session["login_failed_count"] = failed
        _apply_lockout_policy(request, failed)
        msg = "Tài khoản hoặc mật khẩu không đúng."
        if is_ajax:
            return JsonResponse({"success": False, "message": msg, "login_locked_until_ts": _get_login_locked_until_ts(request)}, status=400)
        messages.error(request, msg)
        return _safe_redirect_back_home("login")

    hashed_ok = check_password(password, user.password)
    plain_ok = user.password == password

    if not (hashed_ok or plain_ok):
        failed = int(request.session.get("login_failed_count", 0)) + 1
        request.session["login_failed_count"] = failed
        _apply_lockout_policy(request, failed)
        msg = "Tài khoản hoặc mật khẩu không đúng."
        if is_ajax:
            return JsonResponse({"success": False, "message": msg}, status=400)
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
        messages.error(request, msg)
        return _safe_redirect_back_home("login")

    if plain_ok:
        user.password = make_password(password)
        user.save(update_fields=["password"])

    # successful login -> clear failed attempts and any lockout
    request.session.pop("login_failed_count", None)
    request.session.pop("login_locked_until", None)

    request.session["logged_in_user_id"] = user.id_users
    request.session["logged_in_user_name"] = user.name_users
    request.session["logged_in_user_role"] = (user.role or "").strip().lower()
    request.session.pop("cart_items", None)

    if is_ajax:
        return JsonResponse({"success": True, "message": "Đăng nhập thành công."})
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
    """Xem và chỉnh sửa thông tin tài khoản."""
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

                addr.address_name = address_name
                addr.full_address = full_address
                addr.phone_address = phone_address or None

                if set_default and not addr.is_default:
                    UserAddress.objects.filter(id_users_id=user_id, is_default=True).update(is_default=False)
                    addr.is_default = True

                addr.save(update_fields=[f for f in ["address_name", "full_address", "phone_address", "is_default"] if hasattr(addr, f)])

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


__all__ = [
    "forgot_password",
    "verify_otp",
    "reset_password",
    "register_user",
    "login_user",
    "logout_user",
    "account_info",
]
