from django.db import models


# USERS
class User(models.Model):

    id_users = models.AutoField(primary_key=True, verbose_name="Mã người dùng")
    name_users = models.CharField(max_length=100, verbose_name="Tên người dùng")
    email_users = models.CharField(db_column="email", max_length=100, unique=True, verbose_name="Email")
    password_users = models.CharField(db_column="password", max_length=255, verbose_name="Mật khẩu")
    role = models.CharField(max_length=20, null=True, blank=True, verbose_name="Vai trò")
    created_at_users = models.DateTimeField(null=True, blank=True, verbose_name="Ngày tạo")

    class Meta:
        managed = False
        db_table = "users"
        verbose_name = "Người dùng"
        verbose_name_plural = "Người dùng"

    def __str__(self):
        return f"{self.name_users} ({self.email_users})"


# CATEGORIES
class Category(models.Model):

    id_categories = models.AutoField(primary_key=True, verbose_name="Mã danh mục")
    name_categories = models.CharField(max_length=100, verbose_name="Tên danh mục")

    class Meta:
        managed = False
        db_table = "categories"
        verbose_name = "Danh mục"
        verbose_name_plural = "Danh mục"

    def __str__(self):
        return self.name_categories


# PRODUCTS
class Product(models.Model):

    id_products = models.AutoField(primary_key=True, verbose_name="Mã sản phẩm")
    name_products = models.CharField(max_length=200, verbose_name="Tên sản phẩm")
    brand = models.CharField(max_length=100, null=True, blank=True, verbose_name="Thương hiệu")
    price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Giá")
    stock = models.IntegerField(null=True, blank=True, verbose_name="Tồn kho")
    image = models.CharField(max_length=255, null=True, blank=True, verbose_name="Hình ảnh")
    description = models.TextField(null=True, blank=True, verbose_name="Mô tả")
    created_at_products = models.DateTimeField(null=True, blank=True, verbose_name="Ngày tạo")
    id_categories = models.ForeignKey(
        Category,
        models.DO_NOTHING,
        db_column="id_categories",
        null=True,
        blank=True,
        verbose_name="Danh mục",
    )

    class Meta:
        managed = False
        db_table = "products"
        verbose_name = "Sản phẩm"
        verbose_name_plural = "Sản phẩm"

    def __str__(self):
        return self.name_products


# PRODUCT DETAIL (thông số linh kiện)
class ProductDetail(models.Model):

    id_product_detail = models.AutoField(primary_key=True, verbose_name="Mã thông số")
    spec_name_product = models.CharField(max_length=100, null=True, blank=True, verbose_name="Tên thông số")
    spec_value_product = models.CharField(max_length=200, null=True, blank=True, verbose_name="Giá trị thông số")
    created_at_product_detail = models.DateTimeField(null=True, blank=True, verbose_name="Ngày tạo")
    id_products = models.ForeignKey(
        Product,
        models.DO_NOTHING,
        db_column="id_products",
        null=True,
        blank=True,
        verbose_name="Sản phẩm",
    )

    class Meta:
        managed = False
        db_table = "product_detail"
        verbose_name = "Thông số sản phẩm"
        verbose_name_plural = "Thông số sản phẩm"

    def __str__(self):
        product_name = self.id_products.name_products if self.id_products else "Sản phẩm không xác định"
        spec_name = self.spec_name_product or "Thông số"
        spec_value = self.spec_value_product or ""
        return f"{product_name} - {spec_name}: {spec_value}"


# CART
class Cart(models.Model):

    id_carts = models.AutoField(primary_key=True, verbose_name="Mã giỏ hàng")
    created_at_carts = models.DateTimeField(null=True, blank=True, verbose_name="Ngày tạo")
    id_users = models.ForeignKey(
        User,
        models.DO_NOTHING,
        db_column="id_users",
        null=True,
        blank=True,
        verbose_name="Người dùng",
    )

    class Meta:
        managed = False
        db_table = "carts"
        verbose_name = "Giỏ hàng"
        verbose_name_plural = "Giỏ hàng"

    def __str__(self):
        return f"Giỏ hàng #{self.id_carts}"


# CART ITEMS
class CartItem(models.Model):

    id_cart_items = models.AutoField(primary_key=True, verbose_name="Mã chi tiết giỏ hàng")
    quantity = models.IntegerField(db_column="quantity_cart_items", null=True, blank=True, verbose_name="Số lượng")
    id_carts = models.ForeignKey(
        Cart,
        models.DO_NOTHING,
        db_column="id_carts",
        null=True,
        blank=True,
        verbose_name="Giỏ hàng",
    )
    id_products = models.ForeignKey(
        Product,
        models.DO_NOTHING,
        db_column="id_products",
        null=True,
        blank=True,
        verbose_name="Sản phẩm",
    )

    class Meta:
        managed = False
        db_table = "cart_items"
        verbose_name = "Chi tiết giỏ hàng"
        verbose_name_plural = "Chi tiết giỏ hàng"

    def __str__(self):
        product_name = self.id_products.name_products if self.id_products else "Sản phẩm không xác định"
        return f"Chi tiết giỏ hàng #{self.id_cart_items} - {product_name}"


# ORDERS
class Order(models.Model):

    id_orders = models.AutoField(primary_key=True, verbose_name="Mã đơn hàng")
    id_users = models.ForeignKey(
        User,
        models.DO_NOTHING,
        db_column="id_users",
        null=True,
        blank=True,
        verbose_name="Người dùng",
    )
    total_price = models.DecimalField(
        db_column="total_price_orders",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Tổng tiền",
    )
    status_orders = models.CharField(max_length=50, null=True, blank=True, verbose_name="Trạng thái")
    created_at = models.DateTimeField(db_column="created_at_orders", null=True, blank=True, verbose_name="Ngày tạo")

    class Meta:
        managed = False
        db_table = "orders"
        verbose_name = "Đơn hàng"
        verbose_name_plural = "Đơn hàng"

    def __str__(self):
        return f"Đơn hàng #{self.id_orders}"


# ORDER ITEMS
class OrderItem(models.Model):

    id_order_items = models.AutoField(primary_key=True, verbose_name="Mã chi tiết đơn hàng")
    id_orders = models.ForeignKey(
        Order,
        models.DO_NOTHING,
        db_column="id_orders",
        null=True,
        blank=True,
        verbose_name="Đơn hàng",
    )
    id_products = models.ForeignKey(
        Product,
        models.DO_NOTHING,
        db_column="id_products",
        null=True,
        blank=True,
        verbose_name="Sản phẩm",
    )
    quantity = models.IntegerField(db_column="quantity_order_items", null=True, blank=True, verbose_name="Số lượng")
    price = models.DecimalField(
        db_column="price_order_items",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Đơn giá",
    )

    class Meta:
        managed = False
        db_table = "order_items"
        verbose_name = "Chi tiết đơn hàng"
        verbose_name_plural = "Chi tiết đơn hàng"

    def __str__(self):
        product_name = self.id_products.name_products if self.id_products else "Sản phẩm không xác định"
        return f"Chi tiết đơn hàng #{self.id_order_items} - {product_name}"


# USER BEHAVIOR (AI training)
class UserBehavior(models.Model):

    id_user_behavior = models.AutoField(primary_key=True, verbose_name="Mã hành vi")
    id_users = models.ForeignKey(
        User,
        models.DO_NOTHING,
        db_column="id_users",
        null=True,
        blank=True,
        verbose_name="Người dùng",
    )
    id_products = models.ForeignKey(
        Product,
        models.DO_NOTHING,
        db_column="id_products",
        null=True,
        blank=True,
        verbose_name="Sản phẩm",
    )
    action_type_user_behavior = models.CharField(max_length=50, null=True, blank=True, verbose_name="Loại hành vi")
    created_at = models.DateTimeField(db_column="created_at_user_behavior", null=True, blank=True, verbose_name="Thời gian")

    class Meta:
        managed = False
        db_table = "user_behavior"
        verbose_name = "Hành vi người dùng"
        verbose_name_plural = "Hành vi người dùng"

    def __str__(self):
        action = self.action_type_user_behavior or "Không xác định"
        return f"Hành vi #{self.id_user_behavior} - {action}"


# SEARCH HISTORY
class SearchHistory(models.Model):

    id_search_history = models.AutoField(primary_key=True, verbose_name="Mã lịch sử tìm kiếm")
    keyword_search_history = models.CharField(max_length=255, verbose_name="Từ khóa tìm kiếm")
    created_at = models.DateTimeField(db_column="created_at_search_history", null=True, blank=True, verbose_name="Thời gian")
    id_users = models.ForeignKey(
        User,
        models.DO_NOTHING,
        db_column="id_users",
        null=True,
        blank=True,
        verbose_name="Người dùng",
    )

    class Meta:
        managed = False
        db_table = "search_history"
        verbose_name = "Lịch sử tìm kiếm"
        verbose_name_plural = "Lịch sử tìm kiếm"

    def __str__(self):
        keyword = self.keyword_search_history or ""
        return f"Lịch sử tìm kiếm #{self.id_search_history} - {keyword}"