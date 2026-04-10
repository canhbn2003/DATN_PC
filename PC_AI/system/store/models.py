from django.db import models


# =========================
# USERS
# =========================
class User(models.Model):
    ROLE_CHOICES = [
        ('user', 'Người dùng'),
        ('admin', 'Quản trị viên'),
    ]

    id_users = models.AutoField(primary_key=True)
    name_users = models.CharField(max_length=100)
    email = models.CharField(max_length=100, unique=True)
    password = models.CharField(max_length=255)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, null=True, blank=True)
    gender_users = models.CharField(max_length=10, null=True, blank=True)
    phone_users = models.CharField(max_length=20, null=True, blank=True)
    address_users = models.CharField(max_length=255, null=True, blank=True)
    created_at_users = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "users"
        verbose_name = "Người dùng"
        verbose_name_plural = "Người dùng"

    def __str__(self):
        return self.name_users


# =========================
# CATEGORIES
# =========================
class Category(models.Model):
    id_categories = models.AutoField(primary_key=True)
    name_categories = models.CharField(max_length=100)

    class Meta:
        managed = False
        db_table = "categories"
        verbose_name = "Danh mục"
        verbose_name_plural = "Danh mục"

    def __str__(self):
        return self.name_categories


# =========================
# PRODUCTS
# =========================
class Product(models.Model):
    id_products = models.AutoField(primary_key=True)
    name_products = models.CharField(max_length=200)
    brand = models.CharField(max_length=100, null=True, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    stock = models.IntegerField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    created_at_products = models.DateTimeField(auto_now_add=True)

    id_categories = models.ForeignKey(
        Category,
        models.DO_NOTHING,
        db_column="id_categories",
        null=True,
        blank=True
    )

    class Meta:
        managed = False
        db_table = "products"
        verbose_name = "Sản phẩm"
        verbose_name_plural = "Sản phẩm"

    def __str__(self):
        return self.name_products


# =========================
# PRODUCT IMAGES
# =========================
class ProductImage(models.Model):
    id_product_images = models.AutoField(primary_key=True)

    id_products = models.ForeignKey(
        Product,
        models.CASCADE,
        db_column="id_products",
        related_name="images"
    )

    image_url = models.CharField(max_length=500)
    is_main = models.BooleanField(default=False)
    sort_order = models.IntegerField(default=0)
    created_at_product_images = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "product_images"
        verbose_name = "Ảnh sản phẩm"
        verbose_name_plural = "Ảnh sản phẩm"
        constraints = [
            models.UniqueConstraint(
                fields=["id_products"],
                condition=models.Q(is_main=True),
                name="unique_main_image_per_product"
            )
        ]


# =========================
# CARTS (1 USER = 1 CART)
# =========================
class Cart(models.Model):
    id_carts = models.AutoField(primary_key=True)

    id_users = models.OneToOneField(   # 🔥 QUAN TRỌNG
        User,
        models.CASCADE,
        db_column="id_users"
    )

    created_at_carts = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "carts"
        verbose_name = "Giỏ hàng"
        verbose_name_plural = "Giỏ hàng"


# =========================
# CART ITEMS
# =========================
class CartItem(models.Model):
    id_cart_items = models.AutoField(primary_key=True)

    id_carts = models.ForeignKey(
        Cart,
        models.CASCADE,
        db_column="id_carts",
        related_name="items"
    )

    id_products = models.ForeignKey(
        Product,
        models.DO_NOTHING,
        db_column="id_products"
    )

    quantity_cart_items = models.IntegerField()

    class Meta:
        managed = False
        db_table = "cart_items"
        verbose_name = "Sản phẩm trong giỏ hàng"
        verbose_name_plural = "Sản phẩm trong giỏ hàng"
        constraints = [
            models.UniqueConstraint(
                fields=["id_carts", "id_products"],
                name="unique_product_in_cart"
            )
        ]


# =========================
# ORDERS
# =========================
class Order(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Chờ xử lý'),
        ('confirmed', 'Đã xác nhận'),
        ('shipping', 'Đang giao'),
        ('completed', 'Hoàn thành'),
        ('cancelled', 'Đã hủy'),
    ]

    id_orders = models.AutoField(primary_key=True)

    id_users = models.ForeignKey(
        User,
        models.DO_NOTHING,
        db_column="id_users"
    )

    total_price_orders = models.DecimalField(max_digits=12, decimal_places=2)
    status_orders = models.CharField(max_length=50, choices=STATUS_CHOICES)
    created_at_orders = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "orders"
        verbose_name = "Đơn hàng"
        verbose_name_plural = "Đơn hàng"


# =========================
# ORDER ITEMS
# =========================
class OrderItem(models.Model):
    id_order_items = models.AutoField(primary_key=True)

    id_orders = models.ForeignKey(
        Order,
        models.CASCADE,
        db_column="id_orders",
        related_name="items"
    )

    id_products = models.ForeignKey(
        Product,
        models.DO_NOTHING,
        db_column="id_products"
    )

    quantity_order_items = models.IntegerField()
    price_order_items = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        managed = False
        db_table = "order_items"
        verbose_name = "Sản phẩm trong đơn hàng"
        verbose_name_plural = "Sản phẩm trong đơn hàng"


# =========================
# USER BEHAVIOR
# =========================
class UserBehavior(models.Model):
    ACTION_CHOICES = [
        ('view', 'Xem'),
        ('add_to_cart', 'Thêm vào giỏ'),
        ('purchase', 'Mua hàng'),
    ]

    id_user_behavior = models.AutoField(primary_key=True)

    id_users = models.ForeignKey(
        User,
        models.CASCADE,
        db_column="id_users"
    )

    id_products = models.ForeignKey(
        Product,
        models.CASCADE,
        db_column="id_products"
    )

    action_type_user_behavior = models.CharField(max_length=50, choices=ACTION_CHOICES)
    created_at_user_behavior = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "user_behavior"
        verbose_name = "Hành vi người dùng"
        verbose_name_plural = "Hành vi người dùng"


# =========================
# SEARCH HISTORY
# =========================
class SearchHistory(models.Model):
    id_search_history = models.AutoField(primary_key=True)

    keyword_search_history = models.CharField(max_length=255)
    created_at_search_history = models.DateTimeField(auto_now_add=True)

    id_users = models.ForeignKey(
        User,
        models.CASCADE,
        db_column="id_users"
    )

    class Meta:
        managed = False
        db_table = "search_history"
        verbose_name = "Lịch sử tìm kiếm"
        verbose_name_plural = "Lịch sử tìm kiếm"


# =========================
# DISCOUNTS
# =========================
class Discount(models.Model):
    TYPE_CHOICES = [
        ('percent', 'Phần trăm'),
        ('fixed', 'Số tiền cố định'),
    ]

    APPLY_CHOICES = [
        ('product', 'Sản phẩm'),
        ('category', 'Danh mục'),
        ('all', 'Toàn bộ'),
    ]

    id_discounts = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100)
    discount_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2)
    apply_type = models.CharField(max_length=20, choices=APPLY_CHOICES)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    status = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "discounts"
        verbose_name = "SALE"
        verbose_name_plural = "SALE"


class DiscountProduct(models.Model):
    id = models.AutoField(primary_key=True)

    id_discounts = models.ForeignKey(
        Discount,
        models.CASCADE,
        db_column="id_discounts"
    )

    id_products = models.ForeignKey(
        Product,
        models.CASCADE,
        db_column="id_products"
    )

    class Meta:
        managed = False
        db_table = "discount_products"
        verbose_name = "Sản phẩm SALE"
        verbose_name_plural = "Sản phẩm SALE"


class DiscountCategory(models.Model):
    id = models.AutoField(primary_key=True)

    id_discounts = models.ForeignKey(
        Discount,
        models.CASCADE,
        db_column="id_discounts"
    )

    id_categories = models.ForeignKey(
        Category,
        models.CASCADE,
        db_column="id_categories"
    )

    class Meta:
        managed = False
        db_table = "discount_categories"
        verbose_name = "Danh mục SALE"
        verbose_name_plural = "Danh mục SALE"


# =========================
# PROMOTIONS
# =========================
class Promotion(models.Model):
    TYPE_CHOICES = [
        ('percent', 'Phần trăm'),
        ('fixed', 'Số tiền cố định'),
    ]

    id_promotions = models.AutoField(primary_key=True)
    code = models.CharField(max_length=50, unique=True)
    discount_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2)
    max_discount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    min_order_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    usage_limit = models.IntegerField()
    usage_limit_per_user = models.IntegerField(null=True, blank=True)
    used_count = models.IntegerField(default=0)
    status = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "promotions"
        verbose_name = "Mã giảm giá"
        verbose_name_plural = "Mã giảm giá"


class PromotionProduct(models.Model):
    id = models.AutoField(primary_key=True)

    id_promotions = models.ForeignKey(
        Promotion,
        models.CASCADE,
        db_column="id_promotions"
    )

    id_products = models.ForeignKey(
        Product,
        models.CASCADE,
        db_column="id_products"
    )

    class Meta:
        managed = False
        db_table = "promotion_products"
        verbose_name = "Sản phẩm áp dụng mã giảm giá"
        verbose_name_plural = "Sản phẩm áp dụng mã giảm giá"


class UserPromotion(models.Model):
    id_user_promotions = models.AutoField(primary_key=True)

    id_users = models.ForeignKey(
        User,
        models.CASCADE,
        db_column="id_users"
    )

    id_promotions = models.ForeignKey(
        Promotion,
        models.CASCADE,
        db_column="id_promotions"
    )

    is_saved = models.BooleanField(default=False)
    used_count = models.IntegerField(default=0)
    saved_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "user_promotions"
        verbose_name = "Mã giảm giá của người dùng"
        verbose_name_plural = "Mã giảm giá của người dùng"
        constraints = [
            models.UniqueConstraint(
                fields=["id_users", "id_promotions"],
                name="UQ_user_promotion",
            )
        ]


class UserItemScore(models.Model):
    id_users = models.ForeignKey(
        User,
        models.DO_NOTHING,
        db_column="id_users"
    )

    id_products = models.ForeignKey(
        Product,
        models.DO_NOTHING,
        db_column="id_products"
    )

    score = models.FloatField()

    class Meta:
        managed = False
        db_table = "user_item_scores"
        verbose_name = "Điểm user-sản phẩm"
        verbose_name_plural = "Điểm user-sản phẩm"
        constraints = [
            models.UniqueConstraint(
                fields=["id_users", "id_products"],
                name="pk_user_item_scores",
            )
        ]


class ItemSimilarity(models.Model):
    product_1 = models.ForeignKey(
        Product,
        models.DO_NOTHING,
        db_column="product_1",
        related_name="item_similarity_source",
    )
    product_2 = models.ForeignKey(
        Product,
        models.DO_NOTHING,
        db_column="product_2",
        related_name="item_similarity_target",
    )
    similarity = models.FloatField()

    class Meta:
        managed = False
        db_table = "item_similarity"
        verbose_name = "Độ tương đồng sản phẩm"
        verbose_name_plural = "Độ tương đồng sản phẩm"


class UserSimilarity(models.Model):
    user_1 = models.ForeignKey(
        User,
        models.DO_NOTHING,
        db_column="user_1",
        related_name="user_similarity_source",
    )
    user_2 = models.ForeignKey(
        User,
        models.DO_NOTHING,
        db_column="user_2",
        related_name="user_similarity_target",
    )
    similarity = models.FloatField()

    class Meta:
        managed = False
        db_table = "user_similarity"
        verbose_name = "Độ tương đồng người dùng"
        verbose_name_plural = "Độ tương đồng người dùng"