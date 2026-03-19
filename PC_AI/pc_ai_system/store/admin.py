from django.contrib import admin

from .models import (
	Cart,
	CartItem,
	Category,
	Order,
	OrderItem,
	Product,
	ProductDetail,
	SearchHistory,
	User,
	UserBehavior,
)
admin.site.site_header = "Quản trị LTC"
admin.site.site_title = "Trang quản trị LTC"
admin.site.index_title = "Bảng điều khiển quản trị cửa hàng"


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
	list_display = ("id_users", "name_users", "email_users", "role", "created_at_users")
	search_fields = ("name_users", "email_users", "role")
	list_filter = ("role", "created_at_users")
	ordering = ("id_users",)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
	list_display = ("id_categories", "name_categories")
	search_fields = ("name_categories",)
	ordering = ("id_categories",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
	list_display = ("id_products", "name_products", "brand", "price", "stock", "id_categories")
	search_fields = ("name_products", "brand", "description")
	list_filter = ("id_categories", "brand")
	list_select_related = ("id_categories",)
	ordering = ("id_products",)


@admin.register(ProductDetail)
class ProductDetailAdmin(admin.ModelAdmin):
	list_display = ("id_product_detail", "id_products", "spec_name_product", "spec_value_product", "created_at_product_detail")
	search_fields = ("id_products__name_products", "spec_name_product", "spec_value_product")
	list_filter = ("spec_name_product", "created_at_product_detail")
	list_select_related = ("id_products",)
	ordering = ("id_product_detail",)


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
	list_display = ("id_carts", "id_users", "created_at_carts")
	search_fields = ("id_users__name_users", "id_users__email_users")
	list_filter = ("created_at_carts",)
	list_select_related = ("id_users",)
	ordering = ("id_carts",)


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
	list_display = ("id_cart_items", "id_carts", "id_products", "quantity")
	search_fields = ("id_products__name_products", "id_carts__id_users__name_users")
	list_filter = ("quantity",)
	list_select_related = ("id_carts", "id_products", "id_carts__id_users")
	ordering = ("id_cart_items",)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
	list_display = ("id_orders", "id_users", "total_price", "status_orders", "created_at")
	search_fields = ("id_users__name_users", "id_users__email_users", "status_orders")
	list_filter = ("status_orders", "created_at")
	list_select_related = ("id_users",)
	ordering = ("id_orders",)


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
	list_display = ("id_order_items", "id_orders", "id_products", "quantity", "price")
	search_fields = ("id_products__name_products", "id_orders__id_users__name_users")
	list_filter = ("quantity",)
	list_select_related = ("id_orders", "id_products", "id_orders__id_users")
	ordering = ("id_order_items",)


@admin.register(UserBehavior)
class UserBehaviorAdmin(admin.ModelAdmin):
	list_display = ("id_user_behavior", "id_users", "id_products", "action_type_user_behavior", "created_at")
	search_fields = (
		"id_users__name_users",
		"id_users__email_users",
		"id_products__name_products",
		"action_type_user_behavior",
	)
	list_filter = ("action_type_user_behavior", "created_at")
	list_select_related = ("id_users", "id_products")
	ordering = ("id_user_behavior",)


@admin.register(SearchHistory)
class SearchHistoryAdmin(admin.ModelAdmin):
	list_display = ("id_search_history", "id_users", "keyword_search_history", "created_at")
	search_fields = ("id_users__name_users", "id_users__email_users", "keyword_search_history")
	list_filter = ("created_at",)
	list_select_related = ("id_users",)
	ordering = ("id_search_history",)