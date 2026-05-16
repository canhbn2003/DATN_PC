"""Flash sale views."""

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .views_utils_shared import (
    _build_promotion_card_data,
    _build_promotion_context_with_upcoming,
    _common_page_context,
    _get_saved_promotion_codes,
    _set_saved_promotion_codes,
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

    active_promotions = [
        _build_promotion_card_data(promotion, promotion_context, saved_codes=saved_codes)
        for promotion in sorted(
            promotion_context["active_promotions_by_code"].values(),
            key=lambda item: (item.end_date, item.id_promotions),
        )
    ]

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
            "promotions": active_promotions,
            "active_promotions": active_promotions,
            "upcoming_promotions": upcoming_promotions,
            "saved_promotion_codes": saved_codes,
            "saved_promotion_count": len(saved_codes),
        }
    )
    return render(request, "store/pages/flash_sale.html", context)


__all__ = ["flash_sale_page"]
