"""API endpoint views."""

import json
import os
import re

import requests
from django.conf import settings
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import Product, UserBehavior
from .views_utils_shared import (
    BEHAVIOR_ACTION_ALIASES,
    BEHAVIOR_ALLOWED_ACTIONS,
    GEMINI_CHAT_ENDPOINT,
    _append_session_behavior_event,
    _build_ai_chat_data_context,
    _build_notification_items,
    _get_best_selling_products,
    _get_personalized_products_for_home,
    _get_product_pricing,
    _get_public_categories_queryset,
    _get_public_products_queryset,
    _load_products_by_ordered_ids,
    _pick_primary_image,
    _save_search_history_for_logged_in_user,
    _apply_search_filters,
)


def _resolve_gemini_models():
    raw_models = os.environ.get("GEMINI_MODEL") or str(getattr(settings, "GEMINI_MODEL", "") or "").strip()
    if raw_models:
        return [model.strip() for model in re.split(r"[\s,]+", raw_models) if model.strip()]

    return [
        "gemini-2.0-flash-lite-001",
        "gemini-2.0-flash-lite",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash-001",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-2.5-pro",
    ]


def _list_gemini_models(api_key):
    try:
        response = requests.get(
            f"https://generativelanguage.googleapis.com/v1/models?key={api_key}",
            timeout=30,
        )
    except requests.RequestException as exc:
        return None, str(exc)

    if response.status_code >= 400:
        return None, response.text[:1000]

    try:
        payload = response.json()
    except Exception as exc:
        return None, str(exc)

    models = payload.get("models") or []
    available = []
    for item in models:
        name = item.get("name")
        methods = item.get("supportedGenerationMethods") or []
        if not name:
            continue
        if "generateContent" in methods:
            available.append(name.replace("models/", ""))

    return available, None


def serialize_product(product):
    primary_image = _pick_primary_image(product)
    pricing = _get_product_pricing(product)

    return {
        "id": product.id_products,
        "name": product.name_products,
        "brand": product.brand,
        "price": pricing["final_price"],
        "base_price": pricing["original_price"],
        "discount_amount": pricing["discount_amount"],
        "has_discount": pricing["has_discount"],
        "discount_name": pricing["discount_name"],
        "stock": product.stock,
        "image": primary_image,
        "description": product.description,
        "category_id": product.id_categories_id,
        "category_name": product.id_categories.name_categories if product.id_categories else None,
        "status": product.status,
        "is_discontinued": product.is_discontinued,
    }


@require_GET
def get_products(request):
    products = _get_public_products_queryset().all()
    data = [serialize_product(product) for product in products]
    return JsonResponse(data, safe=False)


@require_GET
def get_product_detail(request, id):
    try:
        product = _get_public_products_queryset().get(id_products=id)
    except Product.DoesNotExist:
        return JsonResponse({"error": "Product not found"}, status=404)

    data = serialize_product(product)
    return JsonResponse(data)


@require_GET
def get_categories(request):
    categories = _get_public_categories_queryset()
    data = []
    for c in categories:
        data.append({"id": c.id_categories, "name": c.name_categories})
    return JsonResponse(data, safe=False)


@require_GET
def search_products(request):
    keyword = (request.GET.get("q") or "").strip()
    selected_category_id = (request.GET.get("category") or "").strip()
    selected_brand = (request.GET.get("brand") or "").strip()
    selected_price_range = (request.GET.get("price_range") or "").strip()

    products = _get_public_products_queryset()

    if selected_category_id.isdigit():
        products = products.filter(id_categories_id=int(selected_category_id))

    products = _apply_search_filters(products, keyword, selected_brand, selected_price_range)

    if not keyword:
        products = products[:50]

    _save_search_history_for_logged_in_user(request, keyword)

    data = [serialize_product(product) for product in products]
    return JsonResponse(data, safe=False)


@require_GET
def search_autocomplete(request):
    keyword = (request.GET.get("q") or "").strip()
    if len(keyword) < 2:
        return JsonResponse({"items": []})

    try:
        limit = int(request.GET.get("limit") or 8)
    except (TypeError, ValueError):
        limit = 8

    limit = max(1, min(limit, 12))
    keyword_lower = keyword.lower()
    tokens = [token for token in keyword_lower.split() if token]

    candidate_products = list(
        _get_public_products_queryset()
        .annotate(total_sold=Coalesce(Sum("orderitem__quantity_order_items"), 0))
        .filter(
            Q(name_products__icontains=keyword)
            | Q(brand__icontains=keyword)
            | Q(id_categories__name_categories__icontains=keyword)
        )
        .order_by("-total_sold", "-id_products")[:60]
    )

    scored_candidates = []
    for product in candidate_products:
        name_value = (product.name_products or "").strip()
        name_lower = name_value.lower()
        brand_value = (product.brand or "").strip()
        brand_lower = brand_value.lower()
        category_name = product.id_categories.name_categories if product.id_categories else ""
        category_lower = category_name.lower() if category_name else ""

        relevance = 0.0
        if name_lower == keyword_lower:
            relevance += 120.0
        if name_lower.startswith(keyword_lower):
            relevance += 65.0
        if keyword_lower in name_lower:
            relevance += 35.0

        if brand_lower.startswith(keyword_lower):
            relevance += 24.0
        elif keyword_lower in brand_lower:
            relevance += 14.0

        if category_lower.startswith(keyword_lower):
            relevance += 14.0
        elif keyword_lower in category_lower:
            relevance += 8.0

        if tokens and all(token in name_lower for token in tokens):
            relevance += 22.0
        elif tokens and any(token in name_lower for token in tokens):
            relevance += 10.0

        popularity = min(float(getattr(product, "total_sold", 0) or 0), 5000.0) / 350.0
        total_score = relevance + popularity

        scored_candidates.append((total_score, int(getattr(product, "total_sold", 0) or 0), product))

    scored_candidates.sort(key=lambda row: (row[0], row[1], row[2].id_products), reverse=True)

    items = []
    for _, _, product in scored_candidates[:limit]:
        image_url = _pick_primary_image(product) or ""
        items.append(
            {
                "id": product.id_products,
                "name": product.name_products,
                "brand": product.brand or "",
                "category": product.id_categories.name_categories if product.id_categories else "",
                "image": image_url,
                "url": f"/products/{product.id_products}/",
            }
        )

    return JsonResponse({"items": items})


@csrf_exempt
@require_POST
def save_behavior(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    required_fields = ("product_id", "action")
    missing_fields = [field for field in required_fields if not body.get(field)]

    if missing_fields:
        return JsonResponse({"error": f"Missing required fields: {', '.join(missing_fields)}"}, status=400)

    try:
        user_id = int(body.get("user_id") or request.session.get("logged_in_user_id") or 0)
        product_id = int(body["product_id"])
    except (TypeError, ValueError):
        return JsonResponse({"error": "user_id and product_id must be integers"}, status=400)

    normalized_action = BEHAVIOR_ACTION_ALIASES.get(body["action"], body["action"])
    if normalized_action not in BEHAVIOR_ALLOWED_ACTIONS:
        return JsonResponse({"error": f"Invalid action '{body['action']}'"}, status=400)

    _append_session_behavior_event(request, product_id, normalized_action)

    if not user_id:
        return JsonResponse({"status": "ok", "scope": "session"})

    UserBehavior.objects.create(
        id_users_id=user_id,
        id_products_id=product_id,
        action_type_user_behavior=normalized_action,
    )

    return JsonResponse({"status": "ok", "scope": "user"})


@require_GET
def session_recommendations(request):
    limit_value = request.GET.get("limit") or "8"
    try:
        limit = max(1, min(int(limit_value), 24))
    except (TypeError, ValueError):
        limit = 8

    recommended_products, recommendation_source = _get_personalized_products_for_home(request, limit=limit)
    rendered_cards = [
        render_to_string("store/components/product_card.html", {"product": product}, request=request)
        for product in recommended_products
    ]

    return JsonResponse({"source": recommendation_source, "items_html": "".join(rendered_cards), "count": len(recommended_products)})


@require_GET
def session_recommendations_more(request):
    try:
        limit = max(1, min(int(request.GET.get("limit") or 12), 24))
    except (TypeError, ValueError):
        limit = 12

    try:
        offset = max(0, int(request.GET.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0

    fetch_limit = offset + limit
    recommended_products, recommendation_source = _get_personalized_products_for_home(request, limit=fetch_limit)
    sliced = recommended_products[offset:offset + limit]

    rendered_cards = [
        render_to_string("store/components/product_card.html", {"product": product}, request=request)
        for product in sliced
    ]

    has_more = len(recommended_products) > (offset + limit)
    return JsonResponse({"source": recommendation_source, "items_html": "".join(rendered_cards), "count": len(sliced), "has_more": has_more})


@require_GET
def best_sellers_api(request):
    try:
        limit = max(1, min(int(request.GET.get("limit") or 12), 24))
    except (TypeError, ValueError):
        limit = 12

    try:
        offset = max(0, int(request.GET.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0

    try:
        window_days = max(1, min(int(request.GET.get("window") or 30), 90))
    except (TypeError, ValueError):
        window_days = 30

    products = _get_best_selling_products(limit=limit + 1, offset=offset, window_days=window_days)
    has_more = len(products) > limit
    if has_more:
        products = products[:limit]

    rendered_cards = [
        render_to_string("store/components/product_card.html", {"product": product}, request=request)
        for product in products
    ]
    return JsonResponse({"items_html": "".join(rendered_cards), "count": len(products), "has_more": has_more})


@csrf_exempt
@require_POST
def ai_data_chat(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    question = str(body.get("question") or "").strip()
    if not question:
        return JsonResponse({"error": "Missing required field: question"}, status=400)

    api_key = os.environ.get("GEMINI_API_KEY") or str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if not api_key:
        return JsonResponse({"error": "GEMINI_API_KEY is not configured"}, status=503)

    context = _build_ai_chat_data_context(question, limit=60)
    budget_text = "khong xac dinh"
    if context.get("budget"):
        budget_text = f"{int(context['budget']):,} VND"

    system_prompt = (
        "Bạn là trợ lý chăm sóc khách hàng của cửa hàng PC AI. "
        "Trả lời ngắn gọn, tự nhiên, đúng trọng tâm dựa trên dữ liệu sản phẩm/danh mục/tồn kho được cung cấp. "
        "Không bịa thêm thông tin ngoài dữ liệu. "
        "Nếu khách hỏi build PC, LUÔN trả về JSON với product_id theo schema sau (không markdown, chỉ JSON): "
        '{\"CPU\": product_id, \"Mainboard\": product_id, \"GPU\": product_id, \"RAM\": product_id, \"SSD\": product_id} '
        "Trước JSON, có thể thêm 1-2 câu giải thích ngắn gọn. "
        "Nếu câu hỏi không liên quan build PC, chỉ trả lời text bình thường."
    )

    user_prompt = (
        f"Câu hỏi của khách: {question}\n"
        f"Mục đích sử dụng: {context['intent']}\n"
        f"Ngân sách dự kiến: {budget_text}\n"
        f"Danh sách danh mục ưu tiên: {context['categories_text']}\n"
        f"Số sản phẩm trong dữ liệu: {context['products_count']} / {context['catalog_count']}\n"
        f"Danh sách sản phẩm phù hợp (tối đa {context['products_count']}):\n{context['products_text']}\n"
        "Yêu cầu: Nếu là câu hỏi build PC, trả lời ngắn (1-2 câu) + JSON cấu hình với product_id. "
        "Nếu không phải build PC, chỉ trả lời text."
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": f"{system_prompt}\n\n{user_prompt}"},
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 800,
        },
    }

    models = _resolve_gemini_models()
    last_error = None
    parsed = None

    for model in models:
        endpoint = GEMINI_CHAT_ENDPOINT.format(model=model)
        try:
            response = requests.post(
                f"{endpoint}?key={api_key}",
                json=payload,
                timeout=60,
            )
        except requests.RequestException as exc:
            last_error = {"error": "Failed to call Gemini API", "details": str(exc)}
            continue

        if response.status_code == 404:
            last_error = {
                "error": "Gemini API error",
                "status": response.status_code,
                "details": response.text[:1000],
                "model": model,
            }
            continue

        if response.status_code in (429, 500, 502, 503, 504):
            last_error = {
                "error": "Gemini API error",
                "status": response.status_code,
                "details": response.text[:1000],
                "model": model,
            }
            continue

        if response.status_code >= 400:
            return JsonResponse({"error": "Gemini API error", "status": response.status_code, "details": response.text[:1000]}, status=502)

        try:
            parsed = response.json()
        except Exception as exc:
            return JsonResponse({"error": "Invalid JSON from Gemini", "details": str(exc)}, status=502)

        if parsed:
            break

    if not parsed:
        available_models, list_error = _list_gemini_models(api_key)
        error_payload = last_error or {"error": "Gemini API error", "details": "No available model."}
        if available_models:
            error_payload["available_models"] = available_models
        elif list_error:
            error_payload["available_models_error"] = list_error
        return JsonResponse(error_payload, status=502)

    candidates = parsed.get("candidates")
    if not candidates or not isinstance(candidates, list):
        return JsonResponse({"error": "Invalid response from Gemini", "details": str(parsed)}, status=502)
    content = candidates[0].get("content", {})
    parts = content.get("parts", [])
    answer = ""
    if parts and isinstance(parts, list):
        answer = str(parts[0].get("text") or "").strip()
    if not answer:
        return JsonResponse({"error": "Empty answer from Gemini"}, status=502)

    config_json = None
    json_match = re.search(r'\{[\s\S]+?\}', answer)
    if json_match:
        try:
            config_json = json.loads(json_match.group(0))
        except Exception:
            config_json = None

    suggested_items = []
    suggested_total_price = 0
    if isinstance(config_json, dict):
        product_ids = []

        def collect_ids(value):
            if isinstance(value, dict):
                for nested in value.values():
                    collect_ids(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect_ids(nested)
            else:
                try:
                    product_id = int(value)
                except (TypeError, ValueError):
                    return
                if product_id not in product_ids:
                    product_ids.append(product_id)

        for value in config_json.values():
            collect_ids(value)

        for product in _load_products_by_ordered_ids(product_ids):
            pricing = _get_product_pricing(product)
            final_price = int(pricing["final_price"]) if pricing.get("final_price") is not None else 0
            suggested_total_price += final_price
            suggested_items.append(
                {
                    "id": product.id_products,
                    "name": product.name_products,
                    "brand": product.brand or "",
                    "price": final_price,
                    "stock": int(product.stock or 0),
                    "url": f"/products/{product.id_products}/",
                }
            )

    return JsonResponse(
        {
            "answer": answer,
            "suggested_config": config_json,
            "suggested_items": suggested_items,
            "suggested_total_price": suggested_total_price,
        }
    )


def _extract_json_payload(text):
    if not text:
        return None

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()

    start_obj = text.find("{")
    end_obj = text.rfind("}")
    if start_obj != -1 and end_obj != -1 and end_obj > start_obj:
        return text[start_obj : end_obj + 1].strip()

    start_arr = text.find("[")
    end_arr = text.rfind("]")
    if start_arr != -1 and end_arr != -1 and end_arr > start_arr:
        return text[start_arr : end_arr + 1].strip()

    return None


@csrf_exempt
@require_POST
def ai_build_pc(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    question = str(body.get("question") or "").strip()
    if not question:
        return JsonResponse({"error": "Missing required field: question"}, status=400)

    api_key = os.environ.get("GEMINI_API_KEY") or str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if not api_key:
        return JsonResponse({"error": "GEMINI_API_KEY is not configured"}, status=503)

    context = _build_ai_chat_data_context(question)
    budget_text = "khong xac dinh"
    if context.get("budget"):
        budget_text = f"{int(context['budget']):,} VND"

    system_prompt = (
        "Ban la tro ly AI tu van build PC. \n"
        "Chi duoc phep dung danh sach san pham cung cap, khong tu biet them. \n"
        "Tra ve DUY NHAT mot JSON hop le, khong bao quanh bang markdown hay giai thich them. \n"
        "Schema JSON: {\"configs\":[{\"title\":string,\"score\":0-100,\"reason\":string,\"pros\":[string],\"cons\":[string],\"items\":[{\"category\":string,\"product_id\":number}]}],\"notes\":string}. \n"
        "Chỉ trả về 1 cấu hình tốt nhất để ngắn gọn. "
        "Moi cau hinh chi nen co toi da 5 items. reason, pros, cons phai rat ngan gon. "
        "Neu thieu linh kien, van de xuat cau hinh gan dung tu linh kien hien co (co the thieu mot vai mon), va ghi ro trong notes."
    )

    user_prompt = (
        f"Cau hoi cua khach: {question}\n"
        f"Muc dich su dung: {context['intent']}\n"
        f"Ngan sach du kien: {budget_text}\n"
        f"Danh muc linh kien hien co: {context['categories_text']}\n"
        f"So san pham trong du lieu: {context['products_count']}\n"
        "Danh sach san pham chi tiet:\n"
        f"{context['products_text']}\n"
        "Hay chon san pham phu hop nhat va tra ve JSON dung schema. Gioi han ngan gon, chi 1 cau hinh tot nhat."
    )

    payload = {
        "contents": [
            {"parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]}
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 4096,
        },
    }

    models = _resolve_gemini_models()
    last_error = None
    parsed = None

    for model in models:
        endpoint = GEMINI_CHAT_ENDPOINT.format(model=model)
        try:
            response = requests.post(
                f"{endpoint}?key={api_key}",
                json=payload,
                timeout=70,
            )
        except requests.RequestException as exc:
            last_error = {"error": "Failed to call Gemini API", "details": str(exc)}
            continue

        if response.status_code == 404:
            last_error = {
                "error": "Gemini API error",
                "status": response.status_code,
                "details": response.text[:1000],
                "model": model,
            }
            continue

        if response.status_code >= 400:
            return JsonResponse({"error": "Gemini API error", "status": response.status_code, "details": response.text[:1000]}, status=502)

        try:
            parsed = response.json()
        except Exception as exc:
            return JsonResponse({"error": "Invalid JSON from Gemini", "details": str(exc)}, status=502)

        if parsed:
            break

    if not parsed:
        available_models, list_error = _list_gemini_models(api_key)
        error_payload = last_error or {"error": "Gemini API error", "details": "No available model."}
        if available_models:
            error_payload["available_models"] = available_models
        elif list_error:
            error_payload["available_models_error"] = list_error
        return JsonResponse(error_payload, status=502)

    candidates = parsed.get("candidates")
    if not candidates or not isinstance(candidates, list):
        return JsonResponse({"error": "Invalid response from Gemini", "details": str(parsed)}, status=502)
    content = candidates[0].get("content", {})
    parts = content.get("parts", [])
    answer_text = ""
    if parts and isinstance(parts, list):
        answer_text = str(parts[0].get("text") or "").strip()
    if not answer_text:
        return JsonResponse({"error": "Empty answer from Gemini"}, status=502)

    json_text = _extract_json_payload(answer_text)
    if not json_text:
        return JsonResponse({"error": "AI response missing JSON", "details": answer_text[:1000]}, status=502)

    try:
        ai_payload = json.loads(json_text)
    except Exception as exc:
        # Try a light cleanup for trailing commas/newlines that Gemini sometimes emits.
        cleaned = re.sub(r",\s*([}\]])", r"\1", json_text)
        cleaned = cleaned.strip()
        try:
            ai_payload = json.loads(cleaned)
        except Exception:
            return JsonResponse({"error": "Invalid JSON from AI", "details": str(exc), "raw": answer_text[:1500]}, status=502)

    configs_raw = ai_payload.get("configs") if isinstance(ai_payload, dict) else None
    if not isinstance(configs_raw, list):
        return JsonResponse({"error": "Invalid JSON schema", "details": ai_payload}, status=502)

    configs_raw = configs_raw[:1]

    all_product_ids = []
    for config in configs_raw:
        items = config.get("items") if isinstance(config, dict) else []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            product_id = item.get("product_id") or item.get("id") or item.get("productId")
            try:
                product_id = int(product_id)
            except (TypeError, ValueError):
                continue
            if product_id not in all_product_ids:
                all_product_ids.append(product_id)

    products = _load_products_by_ordered_ids(all_product_ids)
    products_map = {product.id_products: product for product in products}

    normalized_configs = []
    for index, config in enumerate(configs_raw):
        if not isinstance(config, dict):
            continue

        items = config.get("items") if isinstance(config.get("items"), list) else []
        normalized_items = []
        total_price = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            product_id = item.get("product_id") or item.get("id") or item.get("productId")
            try:
                product_id = int(product_id)
            except (TypeError, ValueError):
                continue

            product = products_map.get(product_id)
            if not product:
                continue

            pricing = _get_product_pricing(product)
            final_price = int(pricing["final_price"]) if pricing.get("final_price") is not None else 0
            total_price += final_price

            normalized_items.append(
                {
                    "category": item.get("category") or (product.id_categories.name_categories if product.id_categories else "Khac"),
                    "product_id": product.id_products,
                    "name": product.name_products,
                    "brand": product.brand or "",
                    "price": final_price,
                    "stock": int(product.stock or 0),
                    "image": _pick_primary_image(product) or "",
                    "url": f"/products/{product.id_products}/",
                }
            )

        if not normalized_items:
            continue

        score_value = config.get("score", 0)
        try:
            score_value = float(score_value)
        except (TypeError, ValueError):
            score_value = 0
        score_value = max(0, min(100, score_value))

        pros = config.get("pros") if isinstance(config.get("pros"), list) else []
        cons = config.get("cons") if isinstance(config.get("cons"), list) else []

        normalized_configs.append(
            {
                "title": str(config.get("title") or f"Cau hinh {index + 1}"),
                "score": score_value,
                "reason": str(config.get("reason") or ""),
                "pros": [str(item) for item in pros if str(item).strip()],
                "cons": [str(item) for item in cons if str(item).strip()],
                "items": normalized_items,
                "total_price": total_price,
                "total_items": len(normalized_items),
            }
        )

    if not normalized_configs:
        def normalize_name(value):
            return re.sub(r"[^a-z0-9]", "", str(value or "").lower())

        required_categories = context.get("required_categories") or []
        available_categories = context.get("available_categories") or []
        available_normalized = {normalize_name(name) for name in available_categories}
        missing_categories = [
            name
            for name in required_categories
            if normalize_name(name) not in available_normalized
        ]

        return JsonResponse(
            {
                "configs": [],
                "notes": str(ai_payload.get("notes") or "Khong du du lieu linh kien de tao cau hinh hoan chinh."),
                "missing_categories": missing_categories,
                "meta": {
                    "intent": context.get("intent"),
                    "budget": context.get("budget"),
                },
            }
        )

    def normalize_name(value):
        return re.sub(r"[^a-z0-9]", "", str(value or "").lower())

    required_categories = context.get("required_categories") or []
    available_categories = context.get("available_categories") or []
    available_normalized = {normalize_name(name) for name in available_categories}
    missing_categories = [
        name
        for name in required_categories
        if normalize_name(name) not in available_normalized
    ]

    return JsonResponse(
        {
            "configs": normalized_configs,
            "notes": str(ai_payload.get("notes") or ""),
            "missing_categories": missing_categories,
            "meta": {
                "intent": context.get("intent"),
                "budget": context.get("budget"),
            },
        }
    )


@require_GET
def notifications_api(request):
    items = _build_notification_items(request, limit=20)
    return JsonResponse({"items": items})


__all__ = [
    "serialize_product",
    "get_products",
    "get_product_detail",
    "get_categories",
    "search_products",
    "search_autocomplete",
    "notifications_api",
    "ai_data_chat",
    "ai_build_pc",
    "save_behavior",
    "session_recommendations",
    "session_recommendations_more",
    "best_sellers_api",
]
