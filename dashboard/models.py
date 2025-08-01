from django.db import models
from django.contrib.auth.models import User

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    is_admin = models.BooleanField(default=False)
    team = models.ForeignKey('Team', on_delete=models.SET_NULL, null=True, blank=True) 
    role = models.CharField(max_length=20, choices=[('manager', 'Manager'), ('tl', 'TL')], default='tl')
    tl_provider = models.CharField(max_length=50, choices=[('gmail', 'Gmail'), ('hotmail', 'Hotmail'), ('yahoo', 'Yahoo')], null=True, blank=True) 

    def __str__(self):
        return f"{self.user.username} - {'Admin' if self.is_admin else 'Team Member'}"

class File(models.Model):
    id = models.AutoField(primary_key=True)
    file_name = models.CharField(max_length=255, null=False)
    date = models.DateTimeField(auto_now_add=True)
    count = models.IntegerField(default=0)
    source = models.CharField(max_length=1, choices=[('A', 'A'), ('B', 'B'), ('C', 'C')], default='A')

    def __str__(self):
        return f"{self.file_name} ({self.source})"

# Placeholder for original Email model (can be removed later after migration)
class Email(models.Model):
    id = models.AutoField(primary_key=True)
    gmail_id = models.CharField(max_length=255, null=False)
    password = models.CharField(max_length=255, blank=True, null=True)
    recovery_email = models.EmailField(max_length=255, blank=True, null=True)
    two_fa_code = models.CharField(max_length=255, blank=True, null=True)
    two_fa_link = models.TextField(blank=True, null=True)
    provider = models.CharField(max_length=50, blank=True, null=True)
    status = models.CharField(max_length=20, choices=[('working', 'working'), ('closed', 'closed')], default='working')
    problem_reason = models.TextField(blank=True, null=True)
    last_checked = models.DateTimeField(blank=True, null=True)
    last_login = models.DateTimeField(blank=True, null=True)
    closure_status = models.CharField(max_length=50, blank=True, null=True)
    closure_requested_at = models.DateTimeField(blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    team = models.ForeignKey('Team', on_delete=models.SET_NULL, null=True, blank=True)
    code = models.CharField(max_length=50, blank=True, null=True)
    file = models.ForeignKey(File, on_delete=models.SET_NULL, null=True, blank=True, related_name='emails')
    source_file_id = models.IntegerField(null=True, blank=True)
    assigned_to = models.ForeignKey(UserProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_emails')

    class Meta:
        db_table = 'emails'

    def __str__(self):
        return self.gmail_id

# New table for Admin dashboard
class AdminEmail(models.Model):
    id = models.AutoField(primary_key=True)
    gmail_id = models.CharField(max_length=255, null=False, unique=True)  # Unique to avoid duplicates
    password = models.CharField(max_length=255, blank=True, null=True)
    recovery_email = models.EmailField(max_length=255, blank=True, null=True)
    two_fa_code = models.CharField(max_length=255, blank=True, null=True)
    two_fa_link = models.TextField(blank=True, null=True)
    provider = models.CharField(max_length=50, blank=True, null=True)
    status = models.CharField(max_length=20, choices=[('working', 'working'), ('closed', 'closed')], default='working')
    problem_reason = models.TextField(blank=True, null=True)
    last_checked = models.DateTimeField(blank=True, null=True)
    last_login = models.DateTimeField(blank=True, null=True)
    closure_status = models.CharField(max_length=50, blank=True, null=True)
    closure_requested_at = models.DateTimeField(blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    team = models.ForeignKey('Team', on_delete=models.SET_NULL, null=True, blank=True)
    code = models.CharField(max_length=50, blank=True, null=True)
    file = models.ForeignKey(File, on_delete=models.SET_NULL, null=True, blank=True, related_name='admin_emails')
    source_file_id = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = 'admin_emails'

    def __str__(self):
        return self.gmail_id

# New table for Manager dashboard
class ManagerEmail(models.Model):
    id = models.AutoField(primary_key=True)
    gmail_id = models.CharField(max_length=255, null=False, unique=True)  # Unique to avoid duplicates
    password = models.CharField(max_length=255, blank=True, null=True)
    recovery_email = models.EmailField(max_length=255, blank=True, null=True)
    two_fa_code = models.CharField(max_length=255, blank=True, null=True)
    two_fa_link = models.TextField(blank=True, null=True)
    provider = models.CharField(max_length=50, blank=True, null=True)
    status = models.CharField(max_length=20, choices=[('working', 'working'), ('closed', 'closed'), ('pending_closed', 'Pending Closed')], default='working')
    problem_reason = models.TextField(blank=True, null=True)
    last_checked = models.DateTimeField(blank=True, null=True)
    last_login = models.DateTimeField(blank=True, null=True)
    closure_status = models.CharField(max_length=50, blank=True, null=True)
    closure_requested_at = models.DateTimeField(blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    team = models.ForeignKey('Team', on_delete=models.SET_NULL, null=True, blank=True)
    code = models.CharField(max_length=50, blank=True, null=True)
    file = models.ForeignKey(File, on_delete=models.SET_NULL, null=True, blank=True, related_name='manager_emails')
    source_file_id = models.IntegerField(null=True, blank=True)
    assigned_to = models.ForeignKey(UserProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_manager_emails')
    is_assigned = models.BooleanField(default=False)  # New field to track assignment status

    class Meta:
        db_table = 'manager_emails'

    def __str__(self):
        return self.gmail_id

# New table for TL dashboard
class TLEmail(models.Model):
    id = models.AutoField(primary_key=True)
    gmail_id = models.CharField(max_length=255, null=False, unique=True)  # Unique to avoid duplicates
    password = models.CharField(max_length=255, blank=True, null=True)
    recovery_email = models.EmailField(max_length=255, blank=True, null=True)
    two_fa_code = models.CharField(max_length=255, blank=True, null=True)
    two_fa_link = models.TextField(blank=True, null=True)
    provider = models.CharField(max_length=50, blank=True, null=True)
    status = models.CharField(max_length=20, choices=[('working', 'working'), ('closed', 'closed'), ('pending_closed', 'Pending Closed')], default='working')
    problem_reason = models.TextField(blank=True, null=True)
    last_checked = models.DateTimeField(blank=True, null=True)
    last_login = models.DateTimeField(blank=True, null=True)
    closure_status = models.CharField(max_length=50, blank=True, null=True)
    closure_requested_at = models.DateTimeField(blank=True, null=True)
    new_password = models.CharField(max_length=255, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    team = models.ForeignKey('Team', on_delete=models.SET_NULL, null=True, blank=True)
    code = models.CharField(max_length=50, blank=True, null=True)
    file = models.ForeignKey(File, on_delete=models.SET_NULL, null=True, blank=True, related_name='tl_emails')
    source_file_id = models.IntegerField(null=True, blank=True)
    assigned_to = models.ForeignKey(UserProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_tl_emails')

    class Meta:
        db_table = 'tl_emails'

    def __str__(self):
        return self.gmail_id

class ClosedEmail(models.Model):
    gmail_id = models.CharField(max_length=255, unique=True)  # Unique to prevent duplicates
    password = models.CharField(max_length=255, blank=True)
    recovery_email = models.CharField(max_length=255, blank=True, null=True)
    new_password = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=20, default='pending_closed')  # Can be 'pending_closed' or 'closed' after verification
    team = models.ForeignKey('Team', on_delete=models.SET_NULL, null=True, blank=True)
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='closed_emails')
    source_file_id = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return self.gmail_id

    class Meta:
        verbose_name = "Closed Email"
        verbose_name_plural = "Closed Emails"

class Team(models.Model):
    TEAM_CHOICES = (
        ('Manager 1', 'Manager 1'),
        ('Manager 2', 'Manager 2'),
    )
    name = models.CharField(max_length=100, choices=TEAM_CHOICES, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name