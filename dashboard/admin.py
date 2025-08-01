from django.contrib import admin

from .models import Email, UserProfile, File, Team

admin.site.register(Email)
admin.site.register(UserProfile)
admin.site.register(File)
admin.site.register(Team)