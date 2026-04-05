from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def vnd_comma(value):
    """Format numeric values as 1,234,567 for VND display."""
    if value is None or value == "":
        return ""

    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return value

    # VND prices are shown without decimal digits.
    return f"{number:,.0f}"
