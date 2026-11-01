from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def page_url(context, number):
    """Keep current filters, change only ?page=."""
    q = context["request"].GET.copy()
    q["page"] = number
    return "?" + q.urlencode()


@register.filter
def inr(value):
    try:
        return "₹%.2f" % float(value)
    except (TypeError, ValueError):
        return "₹0.00"


@register.filter
def who(user):
    return user.display_name if user else "—"
