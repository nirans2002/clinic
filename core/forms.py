from django import forms
from django.utils import timezone


class VersionedModelForm(forms.ModelForm):
    """Optimistic locking: refuse to save if someone else edited the record meanwhile."""

    version = forms.IntegerField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["version"].initial = self.instance.version

    def clean_version(self):
        v = self.cleaned_data.get("version")
        if self.instance.pk and v != self.instance.version:
            who = getattr(self.instance, "updated_by", None)
            when = timezone.localtime(self.instance.updated_at).strftime("%d-%m-%Y %H:%M")
            raise forms.ValidationError(
                f"This record was just changed by {who.display_name if who else 'someone else'} at {when}. "
                "Reload the page to see the latest version, then re-apply your edit."
            )
        return v
