from django.contrib import admin
from django import forms
from django.db.models import Sum
from django.utils.html import format_html_join
from django.utils.safestring import mark_safe
from django.forms.models import modelform_factory

from .models import (
	Cart,
	CartItem,
	Category,
	Order,
	OrderItem,
	Product,
	ProductImage,
	SearchHistory,
	User,
	UserBehavior,
	Discount,
	DiscountProduct,
	DiscountCategory,
	Promotion,
	PromotionProduct,
)
admin.site.site_header = "Quản trị LTC Computer"
admin.site.site_title = "Trang quản trị LTC Computer"
admin.site.index_title = "Bảng điều khiển quản trị cửa hàng"


STATUS_LABELS = {
	"pending": "Chờ xử lý",
	"confirmed": "Đã xác nhận",
	"shipping": "Đang giao",
	"completed": "Hoàn thành",
	"cancelled": "Đã hủy",
}


STATUS_CHOICES = [(code, label) for code, label in STATUS_LABELS.items()]
BOOLEAN_STATUS_CHOICES = (
	(True, "Đang hoạt động"),
	(False, "Ngừng hoạt động"),
)


def format_vi_date(value):
	if not value:
		return ""
	return f"{value.day:02d}/{value.month}/{value.year}"


ALLOWED_STATUS_TRANSITIONS = {
	"pending": {"pending", "confirmed", "cancelled"},
	"confirmed": {"confirmed", "shipping", "cancelled"},
	"shipping": {"shipping", "completed", "cancelled"},
	"completed": {"completed"},
	"cancelled": {"cancelled"},
}


class VietnameseAdminMixin:
	"""Viet hoa nhan tren form nhap lieu trong trang quan tri."""
	field_labels = {}
	list_per_page = 15
	list_max_show_all = 150

	def formfield_for_dbfield(self, db_field, request, **kwargs):
		formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
		if formfield and db_field.name in self.field_labels:
			formfield.label = self.field_labels[db_field.name]
		return formfield


class VietnameseInlineMixin:
	"""Viet hoa nhan tren form inline trong trang quan tri."""
	field_labels = {}

	def formfield_for_dbfield(self, db_field, request, **kwargs):
		formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
		if formfield and db_field.name in self.field_labels:
			formfield.label = self.field_labels[db_field.name]
		return formfield


class TransitionStatusSelect(forms.Select):
	"""Hien thi day du trang thai, nhung khoa (mo di) trang thai khong duoc chon."""

	def __init__(self, *args, allowed_statuses=None, **kwargs):
		super().__init__(*args, **kwargs)
		self.allowed_statuses = set(allowed_statuses or [])

	def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
		option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
		if self.allowed_statuses and str(value) not in self.allowed_statuses:
			option.setdefault("attrs", {})["disabled"] = "disabled"
		return option


class OrderAdminForm(forms.ModelForm):
	class Meta:
		model = Order
		fields = "__all__"

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		if "status_orders" in self.fields:
			allowed = set(code for code, _label in STATUS_CHOICES)
			if self.instance and self.instance.pk:
				current_status = self.instance.status_orders
				allowed = ALLOWED_STATUS_TRANSITIONS.get(current_status, {current_status})

			self.fields["status_orders"].choices = STATUS_CHOICES
			self.fields["status_orders"].widget = TransitionStatusSelect(
				choices=STATUS_CHOICES,
				allowed_statuses=allowed,
			)

	def clean_status_orders(self):
		new_status = self.cleaned_data.get("status_orders")
		if not self.instance or not self.instance.pk:
			return new_status

		current_status = self.instance.status_orders
		allowed = ALLOWED_STATUS_TRANSITIONS.get(current_status, {current_status})
		if new_status not in allowed:
			raise forms.ValidationError(
				f"Không thể chuyển trạng thái từ '{STATUS_LABELS.get(current_status, current_status)}' "
				f"sang '{STATUS_LABELS.get(new_status, new_status)}'."
			)
		return new_status


class DiscountAdminForm(forms.ModelForm):
	class Meta:
		model = Discount
		fields = "__all__"

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		status_field = self.fields.get("status")
		if status_field:
			status_field.widget = forms.Select(choices=BOOLEAN_STATUS_CHOICES)
			status_field.label = "Trạng thái"


class PromotionAdminForm(forms.ModelForm):
	class Meta:
		model = Promotion
		fields = "__all__"

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		status_field = self.fields.get("status")
		if status_field:
			status_field.widget = forms.Select(choices=BOOLEAN_STATUS_CHOICES)
			status_field.label = "Trạng thái"


class DiscountCategoryInline(VietnameseInlineMixin, admin.TabularInline):
	"""Inline để quản lý các danh mục áp dụng chiết khấu"""
	model = DiscountCategory
	extra = 1
	fields = ("id_categories",)
	field_labels = {
		"id_categories": "Danh mục",
	}
	verbose_name = "Danh mục"
	verbose_name_plural = "Danh mục áp dụng"


class DiscountProductInline(VietnameseInlineMixin, admin.TabularInline):
	"""Inline để quản lý các sản phẩm áp dụng chiết khấu"""
	model = DiscountProduct
	extra = 1
	fields = ("id_products",)
	field_labels = {
		"id_products": "Sản phẩm",
	}
	verbose_name = "Sản phẩm"
	verbose_name_plural = "Sản phẩm áp dụng"


class PromotionProductInline(VietnameseInlineMixin, admin.TabularInline):
	"""Inline để quản lý các sản phẩm áp dụng mã giảm giá"""
	model = PromotionProduct
	extra = 1
	fields = ("id_products",)
	field_labels = {
		"id_products": "Sản phẩm",
	}
	verbose_name = "Sản phẩm"
	verbose_name_plural = "Sản phẩm áp dụng"


class ProductPromotionInline(VietnameseInlineMixin, admin.TabularInline):
	"""Inline để chọn mã giảm giá áp dụng cho sản phẩm."""
	model = PromotionProduct
	fk_name = "id_products"
	extra = 1
	fields = ("id_promotions",)
	field_labels = {
		"id_promotions": "Mã giảm giá",
	}
	verbose_name = "Mã giảm giá"
	verbose_name_plural = "Mã giảm giá áp dụng"

	def formfield_for_foreignkey(self, db_field, request, **kwargs):
		if db_field.name == "id_promotions":
			queryset = Promotion.objects.order_by("-status", "code")
			kwargs["queryset"] = queryset
			formfield = super().formfield_for_foreignkey(db_field, request, **kwargs)
			formfield.label_from_instance = lambda obj: f"{obj.code}"
			return formfield
		return super().formfield_for_foreignkey(db_field, request, **kwargs)


class ProductImageInline(VietnameseInlineMixin, admin.TabularInline):
	"""Inline để thêm/sửa ảnh cho sản phẩm."""
	model = ProductImage
	fk_name = "id_products"
	extra = 1
	fields = ("image_url", "is_main", "sort_order")
	field_labels = {
		"image_url": "Đường dẫn ảnh",
		"is_main": "Ảnh chính",
		"sort_order": "Thứ tự hiển thị",
	}
	verbose_name = "Ảnh sản phẩm"
	verbose_name_plural = "Ảnh sản phẩm"



@admin.register(User)
class UserAdmin(VietnameseAdminMixin, admin.ModelAdmin):
	list_display = ("display_id", "display_name", "email", "display_role", "display_created_at")
	search_fields = ("name_users", "email", "role")
	list_filter = ("role", "created_at_users")
	ordering = ("-id_users",)
	list_display_links = ("display_id",)
	field_labels = {
		"id_users": "ID người dùng",
		"name_users": "Họ tên",
		"email": "Email",
		"password": "Mật khẩu",
		"gender_users": "Giới tính",
		"phone_users": "Số điện thoại",
		"address_users": "Địa chỉ",
		"role": "Vai trò",
		"created_at_users": "Ngày tạo",
	}
	verbose_name = "Người dùng"
	verbose_name_plural = "Người dùng"

	@admin.display(description="ID", ordering="id_users")
	def display_id(self, obj):
		return obj.id_users

	@admin.display(description="Họ tên", ordering="name_users")
	def display_name(self, obj):
		return obj.name_users

	@admin.display(description="Vai trò", ordering="role")
	def display_role(self, obj):
		return obj.get_role_display() if hasattr(obj, "get_role_display") else obj.role

	@admin.display(description="Ngày tạo", ordering="created_at_users")
	def display_created_at(self, obj):
		if not obj.created_at_users:
			return ""
		return obj.created_at_users.strftime("%d/%m/%Y %H:%M")


@admin.register(Category)
class CategoryAdmin(VietnameseAdminMixin, admin.ModelAdmin):
	list_display = ("display_id", "display_name")
	search_fields = ("name_categories",)
	ordering = ("-id_categories",)
	list_display_links = ("display_id",)
	field_labels = {
		"id_categories": "ID danh mục",
		"name_categories": "Tên danh mục",
	}
	verbose_name = "Danh mục"
	verbose_name_plural = "Danh mục"

	@admin.display(description="ID", ordering="id_categories")
	def display_id(self, obj):
		return obj.id_categories

	@admin.display(description="Tên danh mục", ordering="name_categories")
	def display_name(self, obj):
		return obj.name_categories


@admin.register(Product)
class ProductAdmin(VietnameseAdminMixin, admin.ModelAdmin):
	inlines = [ProductImageInline, ProductPromotionInline]
	list_display = (
		"display_id",
		"display_name",
		"display_brand",
		"display_price",
		"display_stock",
		"display_category",
		"display_promotion_codes",
	)
	search_fields = ("name_products", "brand", "description")
	list_filter = ("id_categories", "brand")
	list_select_related = ("id_categories",)
	ordering = ("-id_products",)
	list_display_links = ("display_id",)
	field_labels = {
		"id_products": "ID sản phẩm",
		"name_products": "Tên sản phẩm",
		"brand": "Thương hiệu",
		"description": "Mô tả",
		"price": "Giá gốc",
		"discount_price": "Giá giảm",
		"stock": "Tồn kho",
		"image_url": "Ảnh sản phẩm",
		"id_categories": "Danh mục",
	}
	verbose_name = "Sản phẩm"
	verbose_name_plural = "Sản phẩm"

	@admin.display(description="ID", ordering="id_products")
	def display_id(self, obj):
		return obj.id_products

	@admin.display(description="Tên sản phẩm", ordering="name_products")
	def display_name(self, obj):
		return obj.name_products

	@admin.display(description="Thương hiệu", ordering="brand")
	def display_brand(self, obj):
		return obj.brand

	@admin.display(description="Giá", ordering="price")
	def display_price(self, obj):
		return obj.price

	@admin.display(description="Tồn kho", ordering="stock")
	def display_stock(self, obj):
		return obj.stock

	@admin.display(description="Danh mục", ordering="id_categories")
	def display_category(self, obj):
		return obj.id_categories

	def get_queryset(self, request):
		queryset = super().get_queryset(request)
		return queryset.prefetch_related("promotionproduct_set__id_promotions")

	@admin.display(description="Mã giảm giá áp dụng")
	def display_promotion_codes(self, obj):
		codes = [
			item.id_promotions.code
			for item in obj.promotionproduct_set.all()
			if item.id_promotions and item.id_promotions.code
		]
		if not codes:
			return "-"
		return ", ".join(sorted(set(codes)))





# Đăng ký các model mới
@admin.register(Discount)
class DiscountAdmin(VietnameseAdminMixin, admin.ModelAdmin):
	form = DiscountAdminForm
	inlines = [DiscountCategoryInline, DiscountProductInline]
	fields = (
		"name",
		"discount_type",
		"discount_value",
		"apply_type",
		"start_date",
		"end_date",
		"status",
	)
	list_display = (
		"display_id",
		"display_name",
		"display_discount_type",
		"display_discount_value",
		"display_apply_type",
		"display_status",
		"display_start_date",
		"display_end_date",
	)
	search_fields = ("name",)
	list_filter = ("discount_type", "apply_type", "status")
	ordering = ("-id_discounts",)
	list_display_links = ("display_id",)
	field_labels = {
		"name": "Tên chương trình",
		"discount_type": "Loại giảm",
		"discount_value": "Giá trị giảm",
		"apply_type": "Loại áp dụng",
		"start_date": "Ngày bắt đầu",
		"end_date": "Ngày kết thúc",
		"status": "Trạng thái",
	}
	verbose_name = "Khuyến mãi"
	verbose_name_plural = "Khuyến mãi"

	@admin.display(description="ID", ordering="id_discounts")
	def display_id(self, obj):
		return obj.id_discounts

	@admin.display(description="Tên chương trình", ordering="name")
	def display_name(self, obj):
		return obj.name

	@admin.display(description="Loại giảm", ordering="discount_type")
	def display_discount_type(self, obj):
		return obj.get_discount_type_display() if hasattr(obj, "get_discount_type_display") else obj.discount_type

	@admin.display(description="Giá trị giảm", ordering="discount_value")
	def display_discount_value(self, obj):
		return obj.discount_value

	@admin.display(description="Loại áp dụng", ordering="apply_type")
	def display_apply_type(self, obj):
		return obj.get_apply_type_display() if hasattr(obj, "get_apply_type_display") else obj.apply_type

	@admin.display(description="Trạng thái", ordering="status")
	def display_status(self, obj):
		return "Đang hoạt động" if obj.status else "Ngừng hoạt động"

	@admin.display(description="Ngày bắt đầu", ordering="start_date")
	def display_start_date(self, obj):
		return format_vi_date(obj.start_date)

	@admin.display(description="Ngày kết thúc", ordering="end_date")
	def display_end_date(self, obj):
		return format_vi_date(obj.end_date)



@admin.register(Promotion)
class PromotionAdmin(VietnameseAdminMixin, admin.ModelAdmin):
	form = PromotionAdminForm
	inlines = [PromotionProductInline]
	fields = (
		"code",
		"discount_type",
		"discount_value",
		"max_discount",
		"min_order_value",
		"start_date",
		"end_date",
		"usage_limit",
		"usage_limit_per_user",
		"used_count",
		"status",
	)
	list_display = (
		"display_id",
		"display_code",
		"display_discount_type",
		"display_discount_value",
		"display_status",
		"display_start_date",
		"display_end_date",
	)
	search_fields = ("code",)
	list_filter = ("discount_type", "status")
	ordering = ("-id_promotions",)
	list_display_links = ("display_id",)
	field_labels = {
		"code": "Mã giảm giá",
		"discount_type": "Loại giảm",
		"discount_value": "Giá trị giảm",
		"max_discount": "Giảm tối đa",
		"min_order_value": "Giá trị đơn tối thiểu",
		"start_date": "Ngày bắt đầu",
		"end_date": "Ngày kết thúc",
		"usage_limit": "Giới hạn lượt dùng",
		"usage_limit_per_user": "Giới hạn mỗi tài khoản",
		"used_count": "Đã sử dụng",
		"status": "Trạng thái",
	}
	verbose_name = "Mã khuyến mãi"
	verbose_name_plural = "Mã khuyến mãi"

	@admin.display(description="ID", ordering="id_promotions")
	def display_id(self, obj):
		return obj.id_promotions

	@admin.display(description="Mã giảm giá", ordering="code")
	def display_code(self, obj):
		return obj.code

	@admin.display(description="Loại giảm", ordering="discount_type")
	def display_discount_type(self, obj):
		return obj.get_discount_type_display() if hasattr(obj, "get_discount_type_display") else obj.discount_type

	@admin.display(description="Giá trị giảm", ordering="discount_value")
	def display_discount_value(self, obj):
		return obj.discount_value

	@admin.display(description="Trạng thái", ordering="status")
	def display_status(self, obj):
		return "Đang hoạt động" if obj.status else "Ngừng hoạt động"

	@admin.display(description="Ngày bắt đầu", ordering="start_date")
	def display_start_date(self, obj):
		return format_vi_date(obj.start_date)

	@admin.display(description="Ngày kết thúc", ordering="end_date")
	def display_end_date(self, obj):
		return format_vi_date(obj.end_date)


class CartAdmin(VietnameseAdminMixin, admin.ModelAdmin):
	class CartItemInline(VietnameseInlineMixin, admin.TabularInline):
		model = CartItem
		extra = 0
		fields = ("id_products", "quantity_cart_items")
		field_labels = {
			"id_products": "Sản phẩm",
			"quantity_cart_items": "Số lượng",
		}

	inlines = [CartItemInline]
	list_display = ("display_id", "display_user", "cart_products", "total_quantity", "display_created_at")
	search_fields = ("id_users__name_users", "id_users__email")
	list_filter = ("created_at_carts",)
	list_select_related = ("id_users",)
	ordering = ("-id_carts",)
	field_labels = {
		"id_users": "Người dùng",
		"created_at_carts": "Ngày tạo giỏ",
	}

	def get_queryset(self, request):
		queryset = super().get_queryset(request)
		return queryset.prefetch_related("items__id_products")

	def cart_products(self, obj):
		items = list(obj.items.all())
		if not items:
			return "(Giỏ trống)"

		return format_html_join(
			mark_safe("<br>"),
			"{} x{}",
			(
				(
					item.id_products.name_products if item.id_products else "Sản phẩm không xác định",
					item.quantity_cart_items or 0,
				)
				for item in items
			),
		)

	cart_products.short_description = "Sản phẩm trong giỏ hàng"

	@admin.display(description="ID giỏ hàng", ordering="id_carts")
	def display_id(self, obj):
		return obj.id_carts

	@admin.display(description="Người dùng", ordering="id_users")
	def display_user(self, obj):
		return obj.id_users

	@admin.display(description="Ngày tạo giỏ", ordering="created_at_carts")
	def display_created_at(self, obj):
		return obj.created_at_carts

	def total_quantity(self, obj):
		quantity = obj.items.aggregate(total=Sum("quantity_cart_items"))["total"]
		return quantity or 0

	total_quantity.short_description = "Tổng số lượng"


@admin.register(Order)
class OrderAdmin(VietnameseAdminMixin, admin.ModelAdmin):
	class OrderItemInline(VietnameseInlineMixin, admin.TabularInline):
		model = OrderItem
		extra = 0
		fields = ("id_products", "quantity_order_items", "price_order_items", "line_total")
		readonly_fields = ("line_total",)
		field_labels = {
			"id_products": "Sản phẩm",
			"quantity_order_items": "Số lượng",
			"price_order_items": "Đơn giá",
		}

		def line_total(self, obj):
			if not obj:
				return "0"
			return f"{(obj.quantity_order_items or 0) * (obj.price_order_items or 0):,.0f}"

		line_total.short_description = "Thành tiền"

	form = OrderAdminForm
	inlines = [OrderItemInline]
	list_display = (
		"display_order_id",
		"buyer_name",
		"product_summary",
		"total_quantity",
		"display_total_price",
		"status_orders",
		"created_display",
	)
	list_display_links = ("display_order_id",)
	list_editable = ("status_orders",)
	search_fields = (
		"id_users__name_users",
		"id_users__email",
		"status_orders",
		"orderitem__id_products__name_products",
	)
	list_filter = ("status_orders", "created_at_orders")
	list_select_related = ("id_users",)
	ordering = ("-id_orders",)
	readonly_fields = ("created_at_orders",)
	field_labels = {
		"id_users": "Người mua",
		"total_price_orders": "Tổng tiền",
		"status_orders": "Trạng thái",
		"created_at_orders": "Ngày tạo đơn",
	}

	def get_queryset(self, request):
		queryset = super().get_queryset(request)
		return queryset.prefetch_related("items__id_products")

	def buyer_name(self, obj):
		return obj.id_users.name_users if obj.id_users else "Không xác định"

	buyer_name.short_description = "Người mua"
	buyer_name.admin_order_field = "id_users__name_users"

	@admin.display(description="Mã đơn", ordering="id_orders")
	def display_order_id(self, obj):
		return obj.id_orders

	@admin.display(description="Tổng tiền", ordering="total_price_orders")
	def display_total_price(self, obj):
		return obj.total_price_orders

	@admin.display(description="Trạng thái", ordering="status_orders")
	def display_status(self, obj):
		return STATUS_LABELS.get(obj.status_orders, obj.status_orders)

	def buyer_email(self, obj):
		return obj.id_users.email if obj.id_users else "Không xác định"

	buyer_email.short_description = "Email người mua"
	buyer_email.admin_order_field = "id_users__email"

	def product_summary(self, obj):
		items = list(obj.items.all())
		if not items:
			return "(Không có sản phẩm)"

		return format_html_join(
			mark_safe("<br>"),
			"{} x{}",
			(
				(
					item.id_products.name_products if item.id_products else "Sản phẩm không xác định",
					item.quantity_order_items or 0,
				)
				for item in items
			),
		)

	product_summary.short_description = "Sản phẩm đã mua"

	def total_quantity(self, obj):
		quantity = obj.items.aggregate(total=Sum("quantity_order_items"))["total"]
		return quantity or 0

	total_quantity.short_description = "Tổng số lượng"

	def created_display(self, obj):
		if not obj.created_at_orders:
			return ""
		return obj.created_at_orders.strftime("%d/%m/%Y %H:%M")

	created_display.short_description = "Ngày tạo đơn hàng"

	def get_changelist_form(self, request, **kwargs):
		"""Dung cung form voi trang detail de ap quy tac chuyen trang thai 1 chieu."""
		defaults = {"form": OrderAdminForm, "fields": self.list_editable}
		defaults.update(kwargs)
		return modelform_factory(self.model, **defaults)
	
	def save_model(self, request, obj, form, change):
		"""Override để validate status transition khi save từ list_editable."""
		if change and "status_orders" in form.changed_data:
			# Reload object từ database để lấy status cũ
			original = Order.objects.get(pk=obj.pk)
			new_status = obj.status_orders
			old_status = original.status_orders
			
			# Check transition hợp lệ
			allowed = ALLOWED_STATUS_TRANSITIONS.get(old_status, {old_status})
			if new_status not in allowed:
				from django.contrib import messages
				messages.error(
					request,
					f"Không thể chuyển trạng thái từ '{STATUS_LABELS.get(old_status, old_status)}' "
					f"sang '{STATUS_LABELS.get(new_status, new_status)}'."
				)
				return
		
		super().save_model(request, obj, form, change)

@admin.register(UserBehavior)
class UserBehaviorAdmin(VietnameseAdminMixin, admin.ModelAdmin):
	list_display = ("display_id", "display_user", "display_product", "display_action", "display_created_at")
	search_fields = (
		"id_users__name_users",
		"id_users__email",
		"id_products__name_products",
		"action_type_user_behavior",
	)
	list_filter = ("action_type_user_behavior", "created_at_user_behavior")
	list_select_related = ("id_users", "id_products")
	ordering = ("-id_user_behavior",)
	list_display_links = ("display_id",)
	field_labels = {
		"id_users": "Người dùng",
		"id_products": "Sản phẩm",
		"action_type_user_behavior": "Hành động",
		"created_at_user_behavior": "Thời gian",
	}
	verbose_name = "Hành vi người dùng"
	verbose_name_plural = "Hành vi người dùng"

	@admin.display(description="ID", ordering="id_user_behavior")
	def display_id(self, obj):
		return obj.id_user_behavior

	@admin.display(description="Người dùng", ordering="id_users")
	def display_user(self, obj):
		return obj.id_users

	@admin.display(description="Sản phẩm", ordering="id_products")
	def display_product(self, obj):
		return obj.id_products

	@admin.display(description="Hành động", ordering="action_type_user_behavior")
	def display_action(self, obj):
		return obj.get_action_type_user_behavior_display() if hasattr(obj, "get_action_type_user_behavior_display") else obj.action_type_user_behavior

	@admin.display(description="Thời gian", ordering="created_at_user_behavior")
	def display_created_at(self, obj):
		return obj.created_at_user_behavior


@admin.register(SearchHistory)
class SearchHistoryAdmin(VietnameseAdminMixin, admin.ModelAdmin):
	list_display = ("display_id", "display_user", "display_keyword", "display_created_at")
	search_fields = ("id_users__name_users", "id_users__email", "keyword_search_history")
	list_filter = ("created_at_search_history",)
	list_select_related = ("id_users",)
	ordering = ("-id_search_history",)
	list_display_links = ("display_id",)
	field_labels = {
		"id_users": "Người dùng",
		"keyword_search_history": "Từ khóa tìm kiếm",
		"created_at_search_history": "Thời gian",
	}
	verbose_name = "Lịch sử tìm kiếm"
	verbose_name_plural = "Lịch sử tìm kiếm"

	@admin.display(description="ID", ordering="id_search_history")
	def display_id(self, obj):
		return obj.id_search_history

	@admin.display(description="Người dùng", ordering="id_users")
	def display_user(self, obj):
		return obj.id_users

	@admin.display(description="Từ khóa tìm kiếm", ordering="keyword_search_history")
	def display_keyword(self, obj):
		return obj.keyword_search_history

	@admin.display(description="Thời gian", ordering="created_at_search_history")
	def display_created_at(self, obj):
		return obj.created_at_search_history