"""
URL configuration for pc_ai_system project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.views.generic.base import RedirectView
from store.views import (
    cart_page,
    checkout,
    flash_sale_page,
    home_page,
    product_detail_page,
    purchased_products_page,
    viewed_products_page,
)

from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('', home_page, name='home_page'),
    path('products/<int:product_id>/', product_detail_page, name='product_detail_page'),
    path('cart/', cart_page, name='cart_page'),
    path('checkout/', checkout, name='checkout_page'),
    path('flash-sale/', flash_sale_page, name='flash_sale_page'),
    path('viewed-products/', viewed_products_page, name='viewed_products_page'),
    path('purchased-products/', purchased_products_page, name='purchased_products_page'),
    path(
        'admin/store/cartitem/',
        RedirectView.as_view(url='/admin/store/cart/', permanent=False),
        name='admin_cartitem_legacy_redirect',
    ),
    path('admin/', admin.site.urls),
    path('api/', include('store.urls')),
]

# Serve media files during development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
