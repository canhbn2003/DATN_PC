from django.urls import include, path

from . import views


urlpatterns = [
    path('products/', views.get_products, name='get_products'),
    path('products/<int:id>/', views.get_product_detail, name='get_product_detail'),
    path('categories/', views.get_categories, name='get_categories'),
    path('search/', views.search_products, name='search_products'),
    path('behavior/', views.save_behavior, name='save_behavior'),
    path('auth/register/', views.register_user, name='register_user'),
    path('auth/login/', views.login_user, name='login_user'),
    path('auth/logout/', views.logout_user, name='logout_user'),
    path('account/', views.account_info, name='account_info'),
    path('cart/save-to-session/', views.save_cart_to_session, name='save_cart_to_session'),
    path('checkout/', views.checkout, name='checkout'),
    path('orders/', views.order_list, name='order_list'),
    path('orders/<int:order_id>/', views.order_detail, name='order_detail'),
    # path('', include('store.urls')),
]