import json

from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from .models import Category, Product, ProductDetail, UserBehavior
from django.views.decorators.csrf import csrf_exempt


def serialize_product(product):
    return {
        "id": product.id_products,
        "name": product.name_products,
        "brand": product.brand,
        "price": product.price,
        "stock": product.stock,
        "image": product.image,
        "description": product.description,
        "category_id": product.id_categories_id,
        "category_name": product.id_categories.name_categories if product.id_categories else None,
    }


@require_GET
def get_products(request):

    products = Product.objects.select_related("id_categories").all()

    data = [serialize_product(product) for product in products]

    return JsonResponse(data, safe=False)


@require_GET
def get_product_detail(request, id):

    try:
        product = Product.objects.select_related("id_categories").get(id_products=id)
    except Product.DoesNotExist:
        return JsonResponse({"error": "Product not found"}, status=404)

    details = ProductDetail.objects.filter(id_products_id=id).order_by("id_product_detail")

    specs = []
    for detail in details:
        specs.append({
            "name": detail.spec_name_product,
            "value": detail.spec_value_product,
        })

    data = serialize_product(product)
    data["detail"] = specs

    return JsonResponse(data)


@require_GET
def get_categories(request):

    categories = Category.objects.all()

    data = []

    for c in categories:
        data.append({
            "id": c.id_categories,
            "name": c.name_categories
        })

    return JsonResponse(data, safe=False)


@require_GET
def search_products(request):

    keyword = (request.GET.get("q") or "").strip()

    if not keyword:
        return JsonResponse([], safe=False)

    products = Product.objects.select_related("id_categories").filter(name_products__icontains=keyword)

    data = [serialize_product(product) for product in products]

    return JsonResponse(data, safe=False)


@csrf_exempt
@require_POST
def save_behavior(request):

    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    required_fields = ("user_id", "product_id", "action")
    missing_fields = [field for field in required_fields if not body.get(field)]

    if missing_fields:
        return JsonResponse(
            {"error": f"Missing required fields: {', '.join(missing_fields)}"},
            status=400,
        )

    try:
        user_id = int(body["user_id"])
        product_id = int(body["product_id"])
    except (TypeError, ValueError):
        return JsonResponse({"error": "user_id and product_id must be integers"}, status=400)

    UserBehavior.objects.create(
        id_users_id=user_id,
        id_products_id=product_id,
        action_type_user_behavior=body["action"]
    )

    return JsonResponse({"status": "ok"})
