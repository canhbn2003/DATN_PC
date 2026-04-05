from datetime import datetime, time, timedelta
from decimal import Decimal

from django import template
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
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


@register.simple_tag
def admin_dashboard_data():
    today = timezone.localdate()
    start_day = today - timedelta(days=6)
    start_dt = _aware_start_of_day(start_day)

    daily_rows = (
        Order.objects.filter(created_at_orders__gte=start_dt)
        .annotate(day=TruncDate("created_at_orders"))
        .values("day")
        .annotate(order_count=Count("id_orders"), revenue=Sum("total_price_orders"))
        .order_by("day")
    )
    daily_map = {row["day"]: row for row in daily_rows}

    daily_labels = []
    daily_revenue = []
    daily_orders = []
    for offset in range(7):
        day = start_day + timedelta(days=offset)
        row = daily_map.get(day)
        daily_labels.append(day.strftime("%d/%m"))
        daily_revenue.append(float(row["revenue"] or 0) if row else 0)
        daily_orders.append(int(row["order_count"] or 0) if row else 0)

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
    total_revenue = Order.objects.aggregate(total=Sum("total_price_orders"))["total"] or Decimal("0")
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
        "daily_labels": daily_labels,
        "daily_revenue": daily_revenue,
        "daily_orders": daily_orders,
        "status_labels": status_labels,
        "status_values": status_values,
        "top_products_labels": top_products_labels,
        "top_products_values": top_products_values,
        "recent_orders": recent_orders_data,
        "low_stock_products": low_stock_data,
    }