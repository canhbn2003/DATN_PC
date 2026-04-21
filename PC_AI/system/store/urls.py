from django.urls import include, path

from . import views


urlpatterns = [
    path('products/', views.get_products, name='get_products'),
    path('products/<int:id>/', views.get_product_detail, name='get_product_detail'),
    path('categories/', views.get_categories, name='get_categories'),
    path('search/', views.search_products, name='search_products'),
    path('search/autocomplete/', views.search_autocomplete, name='search_autocomplete'),
    path('chat/data/', views.ai_data_chat, name='ai_data_chat'),
    path('behavior/', views.save_behavior, name='save_behavior'),
    path('recommendations/session/', views.session_recommendations, name='session_recommendations'),
    path('auth/register/', views.register_user, name='register_user'),
    path('auth/login/', views.login_user, name='login_user'),
    path('auth/forgot-password/', views.forgot_password, name='forgot_password'),
    path('auth/verify-otp/', views.verify_otp, name='verify_otp'),
    path('auth/reset-password/', views.reset_password, name='reset_password'),
    path('auth/logout/', views.logout_user, name='logout_user'),
    path('account/', views.account_info, name='account_info'),
    path('cart/save-to-session/', views.save_cart_to_session, name='save_cart_to_session'),
    path('cart/load-from-database/', views.load_cart_from_database, name='load_cart_from_database'),
    path('payment/vnpay-return/', views.vnpay_return, name='vnpay_return'),
    path('payment/vnpay-ipn/', views.vnpay_ipn, name='vnpay_ipn'),
    path('checkout/', views.checkout, name='checkout'),
    path('orders/', views.order_list, name='order_list'),
    path('orders/<int:order_id>/', views.order_detail, name='order_detail'),
    # path('', include('store.urls')),
]