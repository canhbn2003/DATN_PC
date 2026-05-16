from datetime import datetime, time, timedelta
from decimal import Decimal

from django import template
from django.db.models import Count, Sum
from django.utils import timezone

from ..models import Category, Discount, Order, OrderItem, Product, Promotion, User


register = template.Library()


STATUS_LABELS = {
    "pending": "Chờ xử lý",
    "confirmed": "Đã xác nhận",
    "shipping": "Đang giao",
    "completed": "Hoàn thành",
    "cancelled": "Đã hủy",
}

STATUS_BADGES = {
    "pending": "warning",
    "confirmed": "info",
    "shipping": "primary",
    "completed": "success",
    "cancelled": "danger",
}


def _format_vnd(value):
    number = Decimal(str(value or 0))
    return f"{number:,.0f} đ"


def _aware_start_of_day(day):
    tz = timezone.get_current_timezone()
    return timezone.make_aware(datetime.combine(day, time.min), tz)


def _month_start(day):
    return day.replace(day=1)


def _shift_month(day, delta_months):
    month_index = (day.year * 12 + (day.month - 1)) + delta_months
    year = month_index // 12
    month = (month_index % 12) + 1
    return day.replace(year=year, month=month, day=1)


def _growth_series(period):
    selected_period = (period or "week").strip().lower()
    if selected_period not in {"week", "month"}:
        selected_period = "week"

    today = timezone.localdate()
    timezone_current = timezone.get_current_timezone()

    if selected_period == "month":
        current_month = _month_start(today)
        month_points = [_shift_month(current_month, offset) for offset in range(-5, 1)]
        month_map = {(point.year, point.month): {"orders": 0, "revenue": Decimal("0")} for point in month_points}

        start_dt = _aware_start_of_day(month_points[0])
        order_rows = (
            Order.objects.filter(status_orders="completed", created_at_orders__gte=start_dt)
            .values_list("created_at_orders", "total_price_orders")
        )

        for created_at, total in order_rows:
            local_day = timezone.localtime(created_at, timezone_current).date()
            key = (local_day.year, local_day.month)
            if key not in month_map:
                continue
            month_map[key]["orders"] += 1
            month_map[key]["revenue"] += Decimal(str(total or 0))

        labels = [point.strftime("%m/%Y") for point in month_points]
        revenues = [float(month_map[(point.year, point.month)]["revenue"]) for point in month_points]
        orders = [month_map[(point.year, point.month)]["orders"] for point in month_points]
        title = "Doanh thu theo tháng"
        subtitle = "So sánh xu hướng doanh thu và đơn hàng trong 6 tháng gần nhất."
        return {
            "selected_period": selected_period,
            "labels": labels,
            "revenue": revenues,
            "orders": orders,
            "title": title,
            "subtitle": subtitle,
        }

    current_week_start = today - timedelta(days=today.weekday())
    week_points = [current_week_start + timedelta(days=offset) for offset in range(7)]
    week_map = {point: {"orders": 0, "revenue": Decimal("0")} for point in week_points}

    start_dt = _aware_start_of_day(week_points[0])
    end_dt = _aware_start_of_day(week_points[-1] + timedelta(days=1))
    order_rows = (
        Order.objects.filter(
            status_orders="completed",
            created_at_orders__gte=start_dt,
            created_at_orders__lt=end_dt,
        ).values_list("created_at_orders", "total_price_orders")
    )

    for created_at, total in order_rows:
        local_day = timezone.localtime(created_at, timezone_current).date()
        if local_day not in week_map:
            continue
        week_map[local_day]["orders"] += 1
        week_map[local_day]["revenue"] += Decimal(str(total or 0))

    day_names = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]
    labels = [f"{day_names[i]} ({point.strftime('%d/%m')})" for i, point in enumerate(week_points)]
    revenues = [float(week_map[point]["revenue"]) for point in week_points]
    orders = [week_map[point]["orders"] for point in week_points]
    title = "Doanh thu theo tuần"
    subtitle = "Biểu đồ doanh thu và đơn hàng từng ngày trong tuần hiện tại."
    return {
        "selected_period": selected_period,
        "labels": labels,
        "revenue": revenues,
        "orders": orders,
        "title": title,
        "subtitle": subtitle,
    }


@register.simple_tag
def admin_dashboard_data(period="week"):
    growth = _growth_series(period)

    status_rows = Order.objects.values("status_orders").annotate(total=Count("id_orders"))
    status_map = {row["status_orders"]: row["total"] for row in status_rows}
    status_labels = [STATUS_LABELS[code] for code in STATUS_LABELS]
    status_values = [status_map.get(code, 0) for code in STATUS_LABELS]

    top_products_rows = (
        OrderItem.objects.values("id_products__name_products")
        .annotate(quantity=Sum("quantity_order_items"))
        .order_by("-quantity", "id_products__name_products")[:6]
    )
    top_products_labels = [row["id_products__name_products"] or "Sản phẩm" for row in top_products_rows]
    top_products_values = [int(row["quantity"] or 0) for row in top_products_rows]

    total_products = Product.objects.count()
    total_categories = Category.objects.count()
    total_users = User.objects.count()
    total_orders = Order.objects.count()
    total_revenue = (
        Order.objects.filter(status_orders="completed").aggregate(total=Sum("total_price_orders"))["total"]
        or Decimal("0")
    )
    active_promotions = Promotion.objects.filter(status=True, start_date__lte=timezone.now(), end_date__gte=timezone.now()).count()
    active_discounts = Discount.objects.filter(status=True, start_date__lte=timezone.now(), end_date__gte=timezone.now()).count()
    pending_orders = Order.objects.filter(status_orders="pending").count()
    low_stock_count = Product.objects.filter(stock__lte=10).count()

    recent_orders = (
        Order.objects.select_related("id_users")
        .annotate(item_count=Sum("items__quantity_order_items"))
        .order_by("-created_at_orders")[:6]
    )
    recent_orders_data = []
    for order in recent_orders:
        recent_orders_data.append(
            {
                "id": order.id_orders,
                "buyer": order.id_users.name_users if order.id_users else "Không xác định",
                "status_code": order.status_orders,
                "status_label": STATUS_LABELS.get(order.status_orders, order.status_orders),
                "status_badge": STATUS_BADGES.get(order.status_orders, "secondary"),
                "total_display": _format_vnd(order.total_price_orders),
                "created_display": timezone.localtime(order.created_at_orders).strftime("%d/%m/%Y %H:%M"),
                "item_count": int(order.item_count or 0),
            }
        )

    low_stock_products = (
        Product.objects.select_related("id_categories")
        .filter(stock__lte=10)
        .order_by("stock", "name_products")[:6]
    )
    low_stock_data = []
    for product in low_stock_products:
        low_stock_data.append(
            {
                "name": product.name_products,
                "brand": product.brand or "N/A",
                "category": product.id_categories.name_categories if product.id_categories else "Khác",
                "stock": product.stock or 0,
            }
        )

    return {
        "metrics": {
            "total_products": total_products,
            "total_categories": total_categories,
            "total_users": total_users,
            "total_orders": total_orders,
            "total_revenue": total_revenue,
            "total_revenue_display": _format_vnd(total_revenue),
            "active_promotions": active_promotions,
            "active_discounts": active_discounts,
            "pending_orders": pending_orders,
            "low_stock_count": low_stock_count,
        },
        "selected_period": growth["selected_period"],
        "growth_title": growth["title"],
        "growth_subtitle": growth["subtitle"],
        "daily_labels": growth["labels"],
        "daily_revenue": growth["revenue"],
        "daily_orders": growth["orders"],
        "status_labels": status_labels,
        "status_values": status_values,
        "top_products_labels": top_products_labels,
        "top_products_values": top_products_values,
        "recent_orders": recent_orders_data,
        "low_stock_products": low_stock_data,
    }