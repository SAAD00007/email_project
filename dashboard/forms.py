from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django import forms
from .models import UserProfile, Team

class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Explicitly clear all validators to avoid default ones
        self.fields['password1'].validators = []
        self.fields['password2'].validators = []
        # Set minimum length only
        self.fields['password1'].min_length = 8
        self.fields['password2'].min_length = 8

    def save(self, commit=True):
        user = super().save(commit=False)
        if commit:
            user.save()
            # Create UserProfile without role or team initially (to be set by admin)
            UserProfile.objects.update_or_create(
                user=user,
                defaults={'is_admin': False, 'team': None}
            )
        return user