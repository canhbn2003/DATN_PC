from django.urls import include, path

# Import view functions from modularized files
from .views_account import (
    forgot_password, verify_otp, reset_password, register_user,
    login_user, logout_user, account_info
)
from .views_api import (
    get_products, get_product_detail, get_categories, search_products,
    search_autocomplete, notifications_api, ai_data_chat, ai_build_pc, save_behavior,
    session_recommendations, session_recommendations_more, best_sellers_api
)
from .views_cart_checkout import (
    cart_page, checkout, save_cart_to_session, load_cart_from_database
)
from .views_orders import order_list, order_detail
from .views_payment import vnpay_return, vnpay_ipn
from .views_flash_sale import flash_sale_page

# Fallback import from main views for any missing functions

urlpatterns = [
    # API endpoints
    path('products/', get_products, name='get_products'),
    path('products/<int:id>/', get_product_detail, name='get_product_detail'),
    path('categories/', get_categories, name='get_categories'),
    path('search/', search_products, name='search_products'),
    path('search/autocomplete/', search_autocomplete, name='search_autocomplete'),
    path('notifications/', notifications_api, name='notifications_api'),
    path('chat/data/', ai_data_chat, name='ai_data_chat'),
    path('chat/build-pc/', ai_build_pc, name='ai_build_pc'),
    path('behavior/', save_behavior, name='save_behavior'),
    path('recommendations/session/', session_recommendations, name='session_recommendations'),
    path('recommendations/session/more/', session_recommendations_more, name='session_recommendations_more'),
    path('best-sellers/', best_sellers_api, name='best_sellers_api'),
    
    # Authentication
    path('auth/register/', register_user, name='register_user'),
    path('auth/login/', login_user, name='login_user'),
    path('auth/forgot-password/', forgot_password, name='forgot_password'),
    path('auth/verify-otp/', verify_otp, name='verify_otp'),
    path('auth/reset-password/', reset_password, name='reset_password'),
    path('auth/logout/', logout_user, name='logout_user'),
    
    # Account management
    path('account/', account_info, name='account_info'),
    
    # Cart and checkout
    path('cart/save-to-session/', save_cart_to_session, name='save_cart_to_session'),
    path('cart/load-from-database/', load_cart_from_database, name='load_cart_from_database'),
    path('checkout/', checkout, name='checkout'),
    
    # Payment
    path('payment/vnpay-return/', vnpay_return, name='vnpay_return'),
    path('payment/vnpay-ipn/', vnpay_ipn, name='vnpay_ipn'),
    
    # Orders
    path('orders/', order_list, name='order_list'),
    path('orders/<int:order_id>/', order_detail, name='order_detail'),
    
    # Public page aliases under /api (legacy)
    path('flash-sale/', flash_sale_page, name='api_flash_sale_page'),
]