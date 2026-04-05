from django import template

register = template.Library()

@register.filter
def get_category(category_list, category_id):
    """
    Trả về danh sách brand_list của category có id = category_id
    """
    for cat in category_list:
        if str(cat.id_categories) == str(category_id):
            return getattr(cat, 'brand_list', [])
    return []
