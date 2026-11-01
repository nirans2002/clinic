from django import forms

from .models import Ticket


class TicketForm(forms.ModelForm):
    description = forms.CharField(max_length=4000, widget=forms.Textarea(attrs={"rows": 8}), label="Describe the problem or request")

    class Meta:
        model = Ticket
        fields = ["subject", "category", "priority", "description"]
        widgets = {"subject": forms.TextInput(attrs={"maxlength": 150})}


class ReplyForm(forms.Form):
    body = forms.CharField(max_length=4000, required=False, label="Reply", widget=forms.Textarea(attrs={"rows": 4}))
    status = forms.ChoiceField(choices=Ticket.Status.choices, required=False)

    def __init__(self, *args, is_admin=False, current=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.current = current
        if is_admin:
            self.fields["status"].initial = current
        else:
            del self.fields["status"]

    def clean(self):
        d = super().clean()
        changed = "status" in self.fields and d.get("status") and d["status"] != self.current
        if not (d.get("body") or "").strip() and not changed:
            raise forms.ValidationError("Write a reply" + (" or change the status." if "status" in self.fields else "."))
        return d
