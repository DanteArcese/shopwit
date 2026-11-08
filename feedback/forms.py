from django import forms

from catalog.models import Offer

from .models import Feedback
from .text import store_key

# Values accepted in the ?type= query string (the report link uses "wrong-data").
TYPE_ALIASES = {
    "wrong-data": Feedback.Kind.DATA,
    **{k.value: k.value for k in Feedback.Kind},
}


class FeedbackForm(forms.Form):
    kind = forms.ChoiceField(
        choices=Feedback.Kind.choices, widget=forms.RadioSelect, label="Type"
    )
    store_name = forms.CharField(
        max_length=120,
        required=False,
        error_messages={"max_length": "Keep the store name to at most 120 characters."},
    )
    website = forms.CharField(
        max_length=200,
        required=False,
        error_messages={"max_length": "Keep the website to at most 200 characters."},
    )
    message = forms.CharField(
        max_length=2000,
        required=False,
        widget=forms.Textarea,
        error_messages={"max_length": "Keep the message to at most 2000 characters."},
    )
    reply_email = forms.EmailField(
        max_length=254,
        required=False,
        error_messages={"invalid": "Enter a valid email address, or leave it blank."},
    )
    offer = forms.IntegerField(required=False, widget=forms.HiddenInput)

    def clean_reply_email(self):
        return self.cleaned_data["reply_email"].lower()

    def clean_offer(self):
        pk = self.cleaned_data.get("offer")
        return Offer.objects.filter(pk=pk).first() if pk else None

    def clean(self):
        data = super().clean()
        kind = data.get("kind")
        if kind == Feedback.Kind.STORE:
            if not store_key(data.get("store_name", "")):
                self.add_error("store_name", "Enter the store's name.")
        elif kind and not data.get("message", "").strip():
            self.add_error("message", "Enter a message.")
        return data

    def save(self) -> Feedback:
        d = self.cleaned_data
        kind = d["kind"]
        is_store = kind == Feedback.Kind.STORE
        return Feedback.objects.create(
            kind=kind,
            message=d["message"].strip(),
            store_name=d["store_name"].strip() if is_store else "",
            website=d["website"].strip() if is_store else "",
            store_key=store_key(d["store_name"]) if is_store else "",
            offer=d["offer"] if kind == Feedback.Kind.DATA else None,
            reply_email=d["reply_email"],
        )
