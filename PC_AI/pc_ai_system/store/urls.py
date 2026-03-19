from django.urls import path

from . import views


urlpatterns = [
    path('products/', views.get_products, name='get_products'),
    path('products/<int:id>/', views.get_product_detail, name='get_product_detail'),
    path('categories/', views.get_categories, name='get_categories'),
    path('search/', views.search_products, name='search_products'),
    path('behavior/', views.save_behavior, name='save_behavior'),
]