from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from .forms import CustomUserCreationForm
from .models import AdminEmail, ClosedEmail, ManagerEmail, TLEmail, UserProfile, Team, File
import pandas as pd
import os
import json
import logging
import math
import io
from django.core.exceptions import ObjectDoesNotExist
from django.views.decorators.csrf import csrf_exempt
from django.db import connection
from django.views.decorators.http import require_GET, require_POST

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def home(request):
    if request.method == 'POST':
        if 'login' in request.POST:
            username = request.POST.get('username')
            password = request.POST.get('password')
            user = authenticate(request, username=username, password=password)
            if user is not None:
                login(request, user)
                profile, _ = UserProfile.objects.get_or_create(user=user)
                if profile.is_admin:
                    return redirect('admin_dashboard')
                else:
                    if not profile.team:
                        return JsonResponse({'message': 'You are not assigned to a team. Please contact an admin.'}, status=403)
                    return redirect('team_dashboard' if profile.role == 'manager' else 'tl_dashboard')
            else:
                return render(request, 'dashboard/home.html', {
                    'error': 'Invalid credentials',
                    'show_register': False,
                    'teams': Team.objects.all()
                })

        elif 'register' in request.POST:
            form = CustomUserCreationForm(request.POST)
            if form.is_valid():
                user = form.save()
                team_name = request.POST.get('team')

                team = None
                if team_name:
                    try:
                        team = Team.objects.get(name=team_name)
                    except Team.DoesNotExist:
                        return render(request, 'dashboard/home.html', {
                            'error': f"Team '{team_name}' does not exist.",
                            'show_register': True,
                            'teams': Team.objects.all()
                        })

                profile, _ = UserProfile.objects.get_or_create(user=user)
                # Do not set is_admin, role, or team here—leave for admin assignment
                profile.team = team
                profile.provider = request.POST.get('provider')
                profile.save()

                login(request, user)
                return render(request, 'dashboard/home.html', {
                    'success': 'Registration was successful! Please wait for an admin to assign your role.',
                    'show_register': True,  # Stay on register page
                    'teams': Team.objects.all()
                })
            else:
                return render(request, 'dashboard/home.html', {
                    'error': form.errors,
                    'show_register': True,
                    'teams': Team.objects.all()
                })

    return render(request, 'dashboard/home.html', {
        'show_register': False,
        'teams': Team.objects.all()
    })


@login_required
def admin_dashboard(request):
    if request.method == 'POST' and request.FILES.getlist('excel_files'):
        files = request.FILES.getlist('excel_files')
        file_ids = request.POST.getlist('file_ids[]')
        sources = request.POST.getlist('sources[]')

        logger.info(f"Received files: {len(files)}, file_ids: {file_ids}, sources: {sources}")
        if len(files) != len(file_ids) or len(files) != len(sources):
            logger.error("Mismatch in uploaded files and metadata.")
            return JsonResponse({"error": "Mismatch between number of files, file IDs, and sources"}, status=400)

        os.makedirs('uploads', exist_ok=True)
        imported_counts = {}
        seen_emails = set()  # Track emails in current upload
        duplicate_emails = set()  # Store all duplicates (current batch + existing)

        # Get existing emails from the table
        existing_emails = set(AdminEmail.objects.values_list('gmail_id', flat=True))

        for file, file_id, source in zip(files, file_ids, sources):
            file_path = os.path.join('uploads', file.name)
            try:
                with open(file_path, 'wb+') as destination:
                    for chunk in file.chunks():
                        destination.write(chunk)

                if not file.name.lower().endswith(('.xlsx', '.xls')):
                    raise ValueError("Unsupported file format. Please upload Excel files (.xlsx or .xls).")

                df_try = pd.read_excel(file_path, header=0, engine='openpyxl')
                header_keywords = ['gmail', 'email', 'gmail_id', 'password', 'recovery', 'provider', 'price']
                has_header = any(str(col).lower() in header_keywords for col in df_try.columns)

                if has_header:
                    df = df_try
                else:
                    df = pd.read_excel(file_path, header=None, engine='openpyxl')

                imported_count = 0
                file_instance = File.objects.create(
                    file_name=file.name,
                    date=pd.Timestamp.now(),
                    count=0,
                    source=source
                )

                for index, row in df.iterrows():
                    email_data = {}
                    try:
                        if has_header:
                            for col in df.columns:
                                value = str(row.get(col, '')).strip() if pd.notna(row.get(col)) else ''
                                col_lower = str(col).lower()
                                if 'gmail' in col_lower:
                                    email_data['gmail_id'] = value
                                elif 'pass' in col_lower:
                                    email_data['password'] = value
                                elif 'recover' in col_lower:
                                    email_data['recovery_email'] = value
                                elif 'provid' in col_lower:
                                    email_data['provider'] = value  # No default yet
                                elif 'price' in col_lower:
                                    try:
                                        email_data['price'] = float(value)
                                    except (ValueError, TypeError):
                                        email_data['price'] = None
                        else:
                            email_data = {
                                'gmail_id': str(row[0]).strip() if pd.notna(row[0]) else '',
                                'password': str(row[1]).strip() if len(row) > 1 and pd.notna(row[1]) else '',
                                'recovery_email': str(row[2]).strip() if len(row) > 2 and pd.notna(row[2]) else '',
                                'provider': str(row[3]).strip() if len(row) > 3 and pd.notna(row[3]) else '',
                                'price': float(row[4]) if len(row) > 4 and pd.notna(row[4]) else None,
                            }

                        # 🛠️ Auto-detect provider from email if not specified
                        if not email_data.get('provider'):
                            email = email_data.get('gmail_id', '')
                            if '@' in email:
                                email_data['provider'] = email.split('@')[-1].split('.')[0].lower()
                            else:
                                email_data['provider'] = 'gmail'

                        gmail_id = email_data.get('gmail_id')
                        if gmail_id:
                            if gmail_id in seen_emails or gmail_id in existing_emails:
                                duplicate_emails.add(gmail_id)
                            else:
                                seen_emails.add(gmail_id)

                            if not AdminEmail.objects.filter(gmail_id=gmail_id).exists():
                                AdminEmail.objects.create(
                                    **email_data,
                                    team=None,
                                    file=file_instance,
                                    source_file_id=int(file_id)
                                )
                                imported_count += 1

                    except Exception as inner_e:
                        logger.warning(f"Skipping row {index} in {file.name} due to error: {inner_e}")
                        continue

                file_instance.count = imported_count
                file_instance.save()
                imported_counts[file.name] = imported_count
                logger.info(f"Imported {imported_count} emails from {file.name}")

            except ValueError as ve:
                logger.error(f"Validation error processing file {file.name}: {str(ve)}")
                return JsonResponse({'error': f'Validation error with file {file.name}: {str(ve)}'}, status=400)
            except Exception as e:
                logger.error(f"Error processing file {file.name}: {str(e)}")
                return JsonResponse({'error': f'Error processing file {file.name}: {str(e)}'}, status=500)
            finally:
                if os.path.exists(file_path):
                    os.remove(file_path)

        total_imported = sum(imported_counts.values())
        teams = list(Team.objects.values_list('id', 'name'))
        context = {
            'message': f'Files uploaded and {total_imported} emails imported successfully.',
            'prompt_team': True if teams and total_imported > 0 else False,
            'teams': teams,
            'duplicate_emails': list(duplicate_emails) if duplicate_emails else None
        }
        return JsonResponse(context)

    emails = AdminEmail.objects.all()
    teams = Team.objects.all()
    return render(request, 'dashboard/admin_dashboard.html', {'emails': emails, 'teams': teams})


@login_required
def team_dashboard(request):
    profile = request.user.userprofile
    if not profile or profile.is_admin:
        return redirect('admin_dashboard')
    team = profile.team
    if not team or profile.role != 'manager':
        return JsonResponse({'message': 'Unauthorized or not a Manager.'}, status=403)

    if request.method == 'POST' and 'delete_email' in request.POST:
        email_id = request.POST.get('email_id')
        try:
            email = ManagerEmail.objects.get(id=email_id, team=team)
            email.delete()
            logger.info(f"Deleted email with id {email_id} by {request.user.username}")
            return JsonResponse({'message': 'Email deleted successfully.'})
        except ManagerEmail.DoesNotExist:
            return JsonResponse({'error': 'Email not found or not authorized.'}, status=404)

    emails = ManagerEmail.objects.filter(team=team)
    return render(request, 'dashboard/team_dashboard.html', {
        'team_name': team.name,
        'emails': emails
    })

@login_required
def team_dashboard_data(request):
    profile = request.user.userprofile
    if not profile or profile.is_admin:
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    team = profile.team
    if not team or profile.role != 'manager':
        return JsonResponse({'message': 'Unauthorized or not a Manager.'}, status=403)

    page = request.GET.get('page', 1)
    search_id = request.GET.get('search_id', '')
    status = request.GET.get('status', '')
    per_page = 10
    try:
        page = int(page)
        start = (page - 1) * per_page
        end = start + per_page
        emails = ManagerEmail.objects.filter(team=team).values('id', 'source_file_id', 'gmail_id', 'password', 'recovery_email', 'provider', 'price', 'status').order_by('source_file_id', 'id')
        
        if search_id and search_id.isdigit():
            emails = emails.filter(source_file_id=int(search_id))
        if status in ['working', 'closed']:
            emails = emails.filter(status=status)
        
        total = emails.count()
        total_pages = math.ceil(total / per_page) if total > 0 else 1
        has_next = end < total
        has_prev = page > 1
        emails_list = list(emails[start:end])
        
        # Calculate provider counts
        provider_counts = {
            'gmail': ManagerEmail.objects.filter(team=team, provider__iexact='gmail').count(),
            'yahoo': ManagerEmail.objects.filter(team=team, provider__iexact='yahoo').count(),
            'hotmail': ManagerEmail.objects.filter(team=team, provider__iexact='hotmail').count()
        }
        
        return JsonResponse({
            'emails': emails_list,
            'total': total,
            'current_page': page,
            'total_pages': total_pages,
            'has_next': has_next,
            'has_prev': has_prev,
            'team_name': team.name,
            'provider_counts': provider_counts
        })
    except ValueError:
        return JsonResponse({'message': 'Invalid page number.'})
@login_required
def tl_dashboard(request):
    profile = request.user.userprofile
    if not profile or profile.is_admin:
        return redirect('admin_dashboard')
    team = profile.team
    if not team or profile.role != 'tl':
        return JsonResponse({'message': 'Unauthorized or not a TL.'}, status=403)
    
    if request.method == 'POST' and request.FILES.get('excel_file'):
        file = request.FILES['excel_file']
        df = pd.read_excel(file, engine='openpyxl')
        
        for index, row in df.iterrows():
            gmail_id = str(row.get('Gmail ID', '')).strip()  # Match export column name
            new_password = str(row.get('New Password', '')).strip()  # Match export column name
            if gmail_id and TLEmail.objects.filter(gmail_id=gmail_id, team=team, assigned_to=profile).exists():
                email = TLEmail.objects.get(gmail_id=gmail_id, team=team, assigned_to=profile)
                email.new_password = new_password
                email.save()
                logger.info(f"Updated new_password for {gmail_id} by {request.user.username}")
        
        emails = TLEmail.objects.filter(team=team, assigned_to=profile)
        return render(request, 'dashboard/tl_dashboard.html', {
            'team_name': team.name,
            'emails': emails
        })

    emails = TLEmail.objects.filter(team=team, assigned_to=profile)
    return render(request, 'dashboard/tl_dashboard.html', {
        'team_name': team.name,
        'emails': emails
    })

@login_required
def tl_dashboard_data(request):
    profile = request.user.userprofile
    if not profile or profile.is_admin:
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    team = profile.team
    if not team or profile.role != 'tl':
        return JsonResponse({'message': 'Unauthorized or not a TL.'}, status=403)

    page = request.GET.get('page', 1)
    search_id = request.GET.get('search_id', '')
    status = request.GET.get('status', '')
    per_page = 10
    try:
        page = int(page)
        start = (page - 1) * per_page
        end = start + per_page
        emails = TLEmail.objects.filter(team=team, assigned_to=profile).values('id', 'source_file_id', 'gmail_id', 'password', 'recovery_email', 'provider', 'new_password', 'status')
        
        if search_id and search_id.isdigit():
            emails = emails.filter(source_file_id=int(search_id))
        if status in ['working', 'closed']:
            emails = emails.filter(status=status)
        
        total = emails.count()
        total_pages = math.ceil(total / per_page) if total > 0 else 1
        has_next = end < total
        has_prev = page > 1
        emails_list = list(emails[start:end])
        
        return JsonResponse({
            'emails': emails_list,
            'total': total,
            'current_page': page,
            'total_pages': total_pages,
            'has_next': has_next,
            'has_prev': has_prev,
            'team_name': team.name
        })
    except ValueError:
        return JsonResponse({'message': 'Invalid page number.'})

@login_required
def get_emails(request):
    """
    Fetch all emails for the email list display or bulk assignment.
    Supports pagination and search by ID. Returns all email IDs from AdminEmail when ?all=true.
    """
    page = request.GET.get('page', 1)
    search_id = request.GET.get('search_id', '')
    all_emails = request.GET.get('all', '') == 'true'
    per_page = 10

    try:
        emails = AdminEmail.objects.all().values('id', 'source_file_id', 'gmail_id', 'password', 'recovery_email', 'provider', 'price', 'team__name').order_by('source_file_id', 'id')
        if search_id and search_id.isdigit():
            emails = emails.filter(source_file_id=int(search_id))

        if all_emails:
            profile = request.user.userprofile
            if not profile.is_admin:
                return JsonResponse({'error': 'Unauthorized to fetch all emails.'}, status=403)
            return JsonResponse({'emails': list(emails.values('id'))})

        page = int(page)
        start = (page - 1) * per_page
        end = start + per_page
        total = emails.count()
        has_next = end < total
        has_prev = page > 1

        return JsonResponse({
            'emails': list(emails[start:end]),
            'total': total,
            'current_page': page,
            'has_next': has_next,
            'has_prev': has_prev
        })
    except ValueError:
        return JsonResponse({'error': 'Invalid page number.'}, status=400)

@login_required
def delete_all_emails(request):
    if request.method == 'POST':
        try:
            profile = request.user.userprofile
            if not profile or not profile.is_admin:
                return JsonResponse({'error': 'Unauthorized to delete all emails.'}, status=403)
            deleted_count, _ = AdminEmail.objects.all().delete()
            logger.info(f"Deleted all {deleted_count} emails by {request.user.username}")
            return JsonResponse({'message': f'All {deleted_count} emails deleted successfully.'})
        except Exception as e:
            logger.error(f"Error deleting all emails: {str(e)}")
            return JsonResponse({'error': 'Error deleting emails.'}, status=500)
    return JsonResponse({'error': 'Invalid request method.'}, status=400)

@login_required
def delete_email(request, email_id):
    if request.method == 'POST':
        try:
            profile = request.user.userprofile
            if not profile or not profile.is_admin:
                return JsonResponse({'error': 'Unauthorized to delete email.'}, status=403)
            email = AdminEmail.objects.get(id=email_id)
            email.delete()
            logger.info(f"Deleted email with id {email_id} by {request.user.username}")
            return JsonResponse({'message': 'Email deleted successfully.'})
        except AdminEmail.DoesNotExist:
            return JsonResponse({'error': 'Email not found.'}, status=404)
    return JsonResponse({'error': 'Invalid request method.'}, status=400)

@login_required
def delete_all_emails_for_team(request):
    if request.method == 'POST':
        try:
            profile = request.user.userprofile
            if not profile or profile.is_admin or profile.role != 'manager':
                return JsonResponse({'error': 'Unauthorized to delete all emails.'}, status=403)
            team = profile.team
            if not team:
                return JsonResponse({'error': 'You are not assigned to a team.'}, status=403)

            deleted_count, _ = ManagerEmail.objects.filter(team=team).delete()
            logger.info(f"Deleted {deleted_count} emails for team {team.name} by {request.user.username}")
            return JsonResponse({'message': f'Successfully deleted {deleted_count} emails for {team.name}.'})
        except Exception as e:
            logger.error(f"Error deleting emails for team: {str(e)}")
            return JsonResponse({'error': 'Error deleting emails.'}, status=500)
    return JsonResponse({'error': 'Invalid request method.'}, status=405)

@login_required
def delete_team_email(request, email_id):
    if request.method == 'POST':
        try:
            profile = request.user.userprofile
            if not profile or profile.is_admin or profile.role != 'manager':
                return JsonResponse({'error': 'Unauthorized to delete email.'}, status=403)
            team = profile.team
            if not team:
                return JsonResponse({'error': 'You are not assigned to a team.'}, status=403)

            email = ManagerEmail.objects.get(id=email_id, team=team)
            email.delete()
            logger.info(f"Deleted email with id {email_id} for team {team.name} by {request.user.username}")
            return JsonResponse({'message': 'Email deleted successfully.'})
        except ManagerEmail.DoesNotExist:
            return JsonResponse({'error': 'Email not found or not authorized.'}, status=404)
        except Exception as e:
            logger.error(f"Error deleting email: {str(e)}")
            return JsonResponse({'error': 'Error deleting email.'}, status=500)
    return JsonResponse({'error': 'Invalid request method.'}, status=405)

@login_required
@require_POST
def delete_tl_email(request, email_id):
    try:
        profile = request.user.userprofile
        if not profile or profile.role != 'tl':
            return JsonResponse({'error': 'Unauthorized to delete email.'}, status=403)
        team = profile.team
        if not team:
            return JsonResponse({'error': 'You are not assigned to a team.'}, status=403)

        email = TLEmail.objects.get(id=email_id, team=team, assigned_to=profile)
        email.delete()
        logger.info(f"Deleted email with id {email_id} for TL {profile.user.username}")
        return JsonResponse({'message': 'Email deleted successfully.'})
    except TLEmail.DoesNotExist:
        return JsonResponse({'error': 'Email not found or not authorized.'}, status=404)
    except Exception as e:
        logger.error(f"Error deleting email: {str(e)}")
        return JsonResponse({'error': 'Error deleting email.'}, status=500)

@login_required
def assign_emails_to_team(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            team_id = data.get('team_id')
            email_ids = data.get('email_ids', [])

            logger.debug(f"Received data: team_id={team_id}, email_ids={email_ids}")
            if not team_id:
                return JsonResponse({'error': 'Team name is required'}, status=400)
            if not email_ids:
                return JsonResponse({'error': 'No email IDs provided'}, status=400)

            valid_teams = ["Manager 1", "Manager 2"]  # Updated to match Team.TEAM_CHOICES
            if team_id not in valid_teams:
                return JsonResponse({'error': f'Invalid team name. Must be one of {valid_teams}'}, status=400)

            team, created = Team.objects.get_or_create(name=team_id)
            emails = AdminEmail.objects.filter(id__in=email_ids, team__isnull=True)
            already_assigned = AdminEmail.objects.filter(id__in=email_ids).exclude(team__isnull=True)

            logger.debug(f"Unassigned emails count: {emails.count()}, Already assigned count: {already_assigned.count()}")
            if not emails.exists() and already_assigned.exists():
                logger.info(f"All selected emails already assigned to a team for team_id={team_id}")
                return JsonResponse({'error': 'The emails were already assigned'}, status=400)
            elif not emails.exists():
                return JsonResponse({'error': 'No valid unassigned emails found'}, status=400)

            updated_count = 0
            manager_count = 0
            tl_count = 0
            for email in emails:
                # Check if gmail_id already exists in ManagerEmail
                if ManagerEmail.objects.filter(gmail_id=email.gmail_id).exists():
                    logger.warning(f"Email {email.gmail_id} already exists in ManagerEmail, skipping assignment")
                    continue
                if not email.team or email.team != team:
                    # Copy to ManagerEmail for the team
                    ManagerEmail.objects.create(
                        gmail_id=email.gmail_id,
                        password=email.password,
                        recovery_email=email.recovery_email,
                        two_fa_code=email.two_fa_code,
                        two_fa_link=email.two_fa_link,
                        provider=email.provider,
                        status=email.status,
                        problem_reason=email.problem_reason,
                        last_checked=email.last_checked,
                        last_login=email.last_login,
                        closure_status=email.closure_status,
                        closure_requested_at=email.closure_requested_at,
                        price=email.price,
                        notes=email.notes,
                        created_at=email.created_at,
                        team=team,
                        code=email.code,
                        file=email.file,
                        source_file_id=email.source_file_id
                    )
                    manager_count += 1
                    updated_count += 1

            return JsonResponse({'message': f'Assigned or updated {updated_count} emails to {team_id}'})
        except Team.DoesNotExist:
            logger.error(f"Team {team_id} does not exist")
            return JsonResponse({'error': f'Team "{team_id}" not found'}, status=400)
        except AdminEmail.DoesNotExist:
            logger.error(f"One or more emails with IDs {email_ids} not found")
            return JsonResponse({'error': 'One or more emails not found'}, status=400)
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON data: {request.body}")
            return JsonResponse({'error': 'Invalid JSON data'}, status=400)
        except Exception as e:
            logger.error(f"Error assigning emails to team: {str(e)}")
            return JsonResponse({'error': str(e)}, status=500)
    return JsonResponse({'error': 'Invalid request method'}, status=405)

@login_required
def export_team_emails(request):
    profile = request.user.userprofile
    if not profile or profile.is_admin or profile.role != 'manager':
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    team = profile.team
    if not team:
        return JsonResponse({'message': 'You are not assigned to a team. Please contact an admin.'}, status=403)

    emails = ManagerEmail.objects.filter(team=team).values(
        'gmail_id', 'password', 'recovery_email', 'provider', 'price', 'status'
    )

    df = pd.DataFrame(list(emails))
    df.rename(columns={
        'gmail_id': 'Gmail ID',
        'password': 'Password',
        'recovery_email': 'Recovery Email',
        'provider': 'Provider',
        'price': 'Price (DH)',
        'status': 'Status'
    }, inplace=True)

    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    df.to_excel(writer, index=False, sheet_name='Team Emails')

    workbook = writer.book
    worksheet = writer.sheets['Team Emails']
    header_format = workbook.add_format({
        'bold': True, 'text_wrap': True, 'valign': 'middle', 'align': 'center',
        'bg_color': '#184d8d', 'font_color': 'white', 'border': 1
    })
    for col_num, value in enumerate(df.columns.values):
        worksheet.write(0, col_num, value, header_format)
    column_widths = [25, 20, 30, 15, 10, 12]
    for i, width in enumerate(column_widths):
        worksheet.set_column(i, i, width)

    writer.close()
    output.seek(0)
    filename = f'team_{team.name}_emails_{pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    response = HttpResponse(
        output, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response

@login_required
def export_tl_emails(request):
    profile = request.user.userprofile
    if not profile or profile.is_admin or profile.role != 'tl':
        logger.error(f"Unauthorized export attempt by user {request.user.username} with role {profile.role if profile else 'None'}")
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    team = profile.team
    if not team:
        logger.error(f"TL {request.user.username} not assigned to a team")
        return JsonResponse({'message': 'You are not assigned to a team. Please contact an admin.'}, status=403)

    emails = TLEmail.objects.filter(assigned_to=profile, team=team).values(
        'gmail_id', 'password', 'recovery_email', 'provider', 'new_password', 'status'
    )

    if not emails.exists():
        logger.warning(f"No TLEmail records found for TL {request.user.username} in team {team.name}")
        return JsonResponse({'message': 'No emails available to export.'}, status=400)

    df = pd.DataFrame(list(emails))
    df.rename(columns={
        'gmail_id': 'Gmail ID',
        'password': 'Password',
        'recovery_email': 'Recovery Email',
        'provider': 'Provider',
        'new_password': 'New Password',  # Replaced Price
        'status': 'Status'
    }, inplace=True)

    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    df.to_excel(writer, index=False, sheet_name='TL Emails')

    workbook = writer.book
    worksheet = writer.sheets['TL Emails']
    header_format = workbook.add_format({
        'bold': True, 'text_wrap': True, 'valign': 'middle', 'align': 'center',
        'bg_color': '#184d8d', 'font_color': 'white', 'border': 1
    })
    for col_num, value in enumerate(df.columns.values):
        worksheet.write(0, col_num, value, header_format)
    column_widths = [25, 20, 30, 15, 20, 12]  # Adjusted for new column
    for i, width in enumerate(column_widths):
        worksheet.set_column(i, i, width)

    writer.close()
    output.seek(0)
    filename = f'tl_{team.name}_emails_{pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    response = HttpResponse(
        output, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    logger.info(f"Export successful for TL {request.user.username}, filename: {filename}")
    return response

@login_required
def import_tl_emails(request):
    profile = request.user.userprofile
    if not profile or profile.is_admin or profile.role != 'tl':
        logger.error(f"Unauthorized import attempt by user {request.user.username} with role {profile.role if profile else 'None'}")
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    team = profile.team
    if not team:
        logger.error(f"TL {request.user.username} not assigned to a team")
        return JsonResponse({'message': 'You are not assigned to a team. Please contact an admin.'}, status=403)

    if request.method == 'POST' and request.FILES.get('excel_file'):
        file = request.FILES['excel_file']
        try:
            df = pd.read_excel(file, engine='openpyxl')
            updated_count = 0
            created_count = 0

            for index, row in df.iterrows():
                gmail_id = str(row.get('Gmail ID', '')).strip()
                # Handle NaN for new_password, convert to empty string if missing
                new_password = str(row.get('New Password', '')).strip() if pd.notna(row.get('New Password')) else ''
                if not gmail_id:
                    continue  # Skip rows with no Gmail ID

                # Check if the email exists for the current TL and team
                email = TLEmail.objects.filter(gmail_id=gmail_id, team=team, assigned_to=profile).first()
                if email:
                    # Update existing email if new_password differs
                    if email.new_password != new_password:
                        email.new_password = new_password
                        email.save()
                        updated_count += 1
                        logger.info(f"Updated new_password for {gmail_id} by {request.user.username}")
                else:
                    # Create new email if it doesn't exist
                    TLEmail.objects.create(
                        gmail_id=gmail_id,
                        new_password=new_password,
                        team=team,
                        assigned_to=profile
                    )
                    created_count += 1
                    logger.info(f"Created new email {gmail_id} with new_password by {request.user.username}")

            # Prepare response message
            message = "Successfully processed import."
            if updated_count > 0:
                message += f" Updated {updated_count} email(s)."
            if created_count > 0:
                message += f" Created {created_count} new email(s)."
            return JsonResponse({'message': message}, status=200)

        except Exception as e:
            logger.error(f"Error processing import for TL {request.user.username}: {str(e)}")
            return JsonResponse({'error': f'Error processing file: {str(e)}'}, status=400)

    return JsonResponse({'error': 'Invalid request method'}, status=405)
@require_GET
@login_required
def admin_files_data(request):
    files = File.objects.filter().values('id', 'file_name', 'date', 'count', 'source')
    return JsonResponse({'files': list(files)})

@require_POST
@login_required
def delete_file(request, file_id):
    try:
        file = File.objects.get(id=file_id)
        AdminEmail.objects.filter(file=file).delete()  # Delete associated emails
        file.delete()
        return JsonResponse({'message': f'File ID {file_id} deleted successfully'})
    except File.DoesNotExist:
        return JsonResponse({'error': 'File not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
    
@require_GET
@login_required
def admin_dashboard_data(request):
    if not request.user.userprofile.is_admin:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    page = request.GET.get('page', 1)
    search_id = request.GET.get('search_id', '')
    per_page = 10
    try:
        page = int(page)
        start = (page - 1) * per_page
        end = start + per_page
        emails = AdminEmail.objects.values(
            'id', 'source_file_id', 'gmail_id', 'password', 'recovery_email', 'provider', 'price'
        ).order_by('source_file_id', 'id')
        
        if search_id and search_id.isdigit():
            emails = emails.filter(source_file_id=int(search_id))
            logger.info(f"Filtering emails by source_file_id={search_id}, query: {emails.query}")
        
        total = emails.count()
        total_pages = math.ceil(total / per_page) if total > 0 else 1
        has_next = end < total
        has_prev = page > 1
        emails_list = list(emails[start:end])
        
        logger.info(f"Admin dashboard data: total={total}, page={page}, search_id={search_id}, emails={emails_list}")
        return JsonResponse({
            'emails': emails_list,
            'total': total,
            'current_page': page,
            'total_pages': total_pages,
            'has_next': has_next,
            'has_prev': has_prev
            # Removed 'teams' as it’s not needed for this view
        })
    except ValueError:
        return JsonResponse({'message': 'Invalid page number.'})

@require_POST
@login_required
def delete_emails_by_source(request, file_id):
    if request.method == 'POST':
        AdminEmail.objects.filter(source_file_id=file_id).delete()
        return JsonResponse({'message': 'Emails deleted successfully'})
    return JsonResponse({'error': 'Invalid request method'}, status=405)

@require_POST
@login_required
def update_team_email_status(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            statuses = data.get('statuses', {})
            profile = request.user.userprofile
            if profile.role != 'manager':
                return JsonResponse({'error': 'Unauthorized to update statuses.'}, status=403)
            team = profile.team
            for email_id, status in statuses.items():
                email = ManagerEmail.objects.get(id=email_id, team=team)
                email.status = status
                email.save()
            return JsonResponse({'message': 'Statuses updated successfully'})
        except ManagerEmail.DoesNotExist:
            return JsonResponse({'error': 'Email not found or not authorized.'}, status=404)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({'error': 'Invalid request method'}, status=405)

@require_POST
@login_required
def update_tl_email_status(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            statuses = data.get('statuses', {})
            profile = request.user.userprofile
            if profile.role != 'tl':
                return JsonResponse({'error': 'Unauthorized to update statuses.'}, status=403)
            team = profile.team
            for email_id, status in statuses.items():
                email = TLEmail.objects.get(id=email_id, team=team, assigned_to=profile)
                email.status = status
                email.save()
            return JsonResponse({'message': 'Statuses updated successfully'})
        except TLEmail.DoesNotExist:
            return JsonResponse({'error': 'Email not found or not authorized.'}, status=404)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({'error': 'Invalid request method'}, status=405)

@login_required
@require_POST
def assign_emails_to_tls(request):
    try:
        # Check if the user is a manager
        user_profile = request.user.userprofile
        if user_profile.role != 'manager':
            return JsonResponse({'error': 'Only managers can assign emails.'}, status=403)

        # Get the manager's team
        team = user_profile.team
        if not team:
            return JsonResponse({'error': 'Manager is not assigned to a team.'}, status=400)
        logger.info(f"Processing for team: {team.name} (ID: {team.id})")

        # Get all emails for the manager's team (not filtered by is_assigned)
        all_emails = ManagerEmail.objects.filter(team=team)
        logger.info(f"Found {all_emails.count()} emails for team {team.name}: {[email.gmail_id for email in all_emails]}")

        if not all_emails.exists():
            logger.info(f"No emails available for team {team.name}")
            return JsonResponse({'message': 'No emails to assign.'})

        # Get all TLs for the team with their provider
        tls = UserProfile.objects.filter(team=team, role='tl').select_related('user')
        logger.info(f"Found {tls.count()} TLs for team {team.name}: {[tl.tl_provider for tl in tls if tl.tl_provider]}")
        if not tls.exists():
            return JsonResponse({'error': 'No Team Leads found for this team.'}, status=400)

        # Map TLs by provider
        tl_by_provider = {tl.tl_provider.lower(): tl for tl in tls if tl.tl_provider}

        assigned_count = 0
        for email in all_emails:
            provider = email.provider.lower() if email.provider else None
            logger.info(f"Processing email {email.gmail_id} with provider {provider}, is_assigned={email.is_assigned}")
            if provider and provider in tl_by_provider:
                tl = tl_by_provider[provider]
                # Check if gmail_id already exists in TLEmail for this team
                if TLEmail.objects.filter(gmail_id=email.gmail_id, team=team).exists():
                    logger.warning(f"Email {email.gmail_id} already assigned to a TL, skipping")
                    continue
                # Create TLEmail record for the TL
                TLEmail.objects.create(
                    gmail_id=email.gmail_id,
                    password=email.password,
                    recovery_email=email.recovery_email,
                    two_fa_code=email.two_fa_code,
                    two_fa_link=email.two_fa_link,
                    provider=email.provider,
                    status=email.status,
                    problem_reason=email.problem_reason,
                    last_checked=email.last_checked,
                    last_login=email.last_login,
                    closure_status=email.closure_status,
                    closure_requested_at=email.closure_requested_at,
                    new_password='',
                    notes=email.notes,
                    created_at=email.created_at,
                    team=team,
                    code=email.code,
                    file=email.file,
                    source_file_id=email.source_file_id,
                    assigned_to=tl
                )
                # Mark the ManagerEmail as assigned
                email.is_assigned = True
                email.save()
                assigned_count += 1
                logger.info(f"Assigned {email.gmail_id} to TL {tl.user.username}")
            else:
                logger.warning(f"Skipping email {email.gmail_id} with provider {provider} - No matching TL")

        if assigned_count > 0:
            logger.info(f"Successfully assigned {assigned_count} emails to TLs for team {team.name}")
            return JsonResponse({'message': f'Assigned or updated {assigned_count} emails to TLs'})
        else:
            # Check if all emails were skipped due to existing TLEmail records
            all_skipped_due_to_assignment = all(TLEmail.objects.filter(gmail_id=email.gmail_id, team=team).exists() for email in all_emails)
            if all_skipped_due_to_assignment and all_emails.exists():
                logger.info(f"All emails for team {team.name} already assigned to TLs")
                return JsonResponse({'error': 'The emails were already assigned'}, status=400)
            logger.info(f"No emails assigned for team {team.name} due to provider mismatch or no TLs")
            return JsonResponse({'message': 'No emails were assigned (no matching TLs for providers or all already assigned).'})
    except Exception as e:
        logger.error(f"Error assigning emails to TLs: {str(e)}")
        return JsonResponse({'error': f'An error occurred during assignment: {str(e)}'}, status=500)

@login_required
def delete_all_tl_emails(request):
    profile = request.user.userprofile
    if not profile or profile.is_admin or profile.role != 'tl':
        logger.error(f"Unauthorized delete all attempt by user {request.user.username} with role {profile.role if profile else 'None'}")
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    team = profile.team
    if not team:
        logger.error(f"TL {request.user.username} not assigned to a team")
        return JsonResponse({'message': 'You are not assigned to a team. Please contact an admin.'}, status=403)

    try:
        emails_deleted = TLEmail.objects.filter(assigned_to=profile, team=team).delete()
        if emails_deleted[0] > 0:
            logger.info(f"Deleted {emails_deleted[0]} emails for TL {request.user.username}")
            return JsonResponse({'message': f'Successfully deleted {emails_deleted[0]} email(s).'})
        else:
            logger.warning(f"No emails found to delete for TL {request.user.username}")
            return JsonResponse({'message': 'No emails found to delete.'})
    except Exception as e:
        logger.error(f"Error deleting all TL emails for {request.user.username}: {str(e)}")
        return JsonResponse({'error': 'An error occurred while deleting emails.'}, status=500)

@login_required
def closed_emails_page(request):
    profile = request.user.userprofile
    if not profile or profile.is_admin:
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    team = profile.team
    if not team:
        return JsonResponse({'message': 'You are not assigned to a team.'}, status=403)

    if request.method == 'POST' and request.FILES.get('closed_file'):
        file = request.FILES['closed_file']
        try:
            os.makedirs('uploads', exist_ok=True)
            file_path = os.path.join('uploads', file.name)
            with open(file_path, 'wb+') as destination:
                for chunk in file.chunks():
                    destination.write(chunk)

            if not file.name.lower().endswith(('.xlsx', '.xls')):
                raise ValueError("Unsupported file format. Please upload Excel files (.xlsx or .xls).")

            df_try = pd.read_excel(file_path, header=0, engine='openpyxl')
            header_keywords = ['gmail', 'email', 'gmail_id', 'password', 'recover', 'new_password']
            has_header = any(str(col).lower() in header_keywords for col in df_try.columns)
            logger.debug(f"Detected columns: {df_try.columns}, has_header: {has_header}")

            if has_header:
                df = df_try
            else:
                df = pd.read_excel(file_path, header=None, engine='openpyxl')

            imported_count = 0
            skipped_count = 0
            for index, row in df.iterrows():
                email_data = {}
                try:
                    if has_header:
                        for col in df.columns:
                            value = str(row.get(col, '')).strip() if pd.notna(row.get(col)) else ''
                            col_lower = str(col).lower()
                            if 'gmail' in col_lower:
                                email_data['gmail_id'] = value
                            elif 'pass' in col_lower:
                                email_data['password'] = value
                            elif 'recover' in col_lower:
                                email_data['recovery_email'] = value
                            elif 'new_pass' in col_lower:
                                email_data['new_password'] = value
                    else:
                        email_data = {
                            'gmail_id': str(row[0]).strip() if pd.notna(row[0]) else '',
                            'password': str(row[1]).strip() if len(row) > 1 and pd.notna(row[1]) else '',
                            'recovery_email': str(row[2]).strip() if len(row) > 2 and pd.notna(row[2]) else '',
                            'new_password': str(row[3]).strip() if len(row) > 3 and pd.notna(row[3]) else '',
                        }

                    gmail_id = email_data.get('gmail_id')
                    if gmail_id:
                        logger.debug(f"Processing gmail_id: {gmail_id}")
                        existing_email = ClosedEmail.objects.filter(gmail_id=gmail_id).first()
                        if existing_email and existing_email.assigned_to != profile.user:
                            skipped_count += 1
                            logger.debug(f"Skipped {gmail_id}, already assigned to another user")
                            continue
                        email, created = ClosedEmail.objects.update_or_create(
                            gmail_id=gmail_id,
                            defaults={
                                'password': email_data.get('password', ''),
                                'recovery_email': email_data.get('recovery_email'),
                                'new_password': email_data.get('new_password', ''),
                                'status': 'pending_closed',
                                'team': team,
                                'assigned_to': profile.user
                            }
                        )
                        if created or email.status != 'pending_closed':
                            email.status = 'pending_closed'
                            email.save()
                            imported_count += 1
                            logger.debug(f"Successfully processed {gmail_id}")
                        else:
                            logger.debug(f"Skipped {gmail_id}, already pending_closed")
                except Exception as inner_e:
                    logger.warning(f"Skipping row {index} in {file.name} due to error: {inner_e}")
                    continue

            message = f'Processed {imported_count} email(s) as pending_closed.'
            if skipped_count > 0:
                message += f' {skipped_count} email(s) were already uploaded by another user and skipped.'
            return JsonResponse({'message': message}, status=200)
        except ValueError as ve:
            logger.error(f"Validation error processing file {file.name}: {str(ve)}")
            return JsonResponse({'error': f'Validation error with file {file.name}: {str(ve)}'}, status=400)
        except Exception as e:
            logger.error(f"Error processing file {file.name}: {str(e)}")
            return JsonResponse({'error': f'Error processing file {file.name}: {str(e)}'}, status=500)
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)

    # Render different templates based on role
    template = 'dashboard/closed_emails.html' if profile.role == 'tl' else 'dashboard/manager_closed_emails.html'
    return render(request, template, {'team_name': team.name})

@login_required
def closed_emails_data(request):
    profile = request.user.userprofile
    logger.debug(f"Profile for user {request.user.username}: {profile}")
    if not profile or profile.is_admin:
        logger.error("Unauthorized access to closed_emails_data")
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    team = profile.team
    logger.debug(f"Team for profile: {team}")
    if not team:
        logger.error("No team assigned to profile")
        return JsonResponse({'message': 'You are not assigned to a team.'}, status=403)

    page = request.GET.get('page', 1)
    per_page = 10
    start = (int(page) - 1) * per_page
    end = start + per_page
    try:
        emails = ClosedEmail.objects.filter(team=team, assigned_to=profile.user, status__in=['pending_closed', 'closed']).values(
            'id', 'source_file_id', 'gmail_id', 'password', 'recovery_email', 'new_password', 'status'
        )[start:end]
        logger.debug(f"Fetched emails: {list(emails)}")
        total = ClosedEmail.objects.filter(team=team, assigned_to=profile.user, status__in=['pending_closed', 'closed']).count()
        logger.debug(f"Total emails count: {total}")
        total_pages = (total + per_page - 1) // per_page
        logger.debug(f"Total pages: {total_pages}")
    except Exception as e:
        logger.error(f"Database query failed: {str(e)}")
        return JsonResponse({'error': 'An error occurred while fetching emails.'}, status=500)

    return JsonResponse({
        'emails': list(emails),
        'current_page': int(page),
        'total_pages': total_pages,
        'has_prev': int(page) > 1,
        'has_next': int(page) < total_pages
    })

# Reused delete endpoint
@login_required
def delete_closed_email(request, email_id):
    profile = request.user.userprofile
    if not profile or profile.role not in ['tl', 'manager']:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    try:
        email = ClosedEmail.objects.get(id=email_id, assigned_to=profile.user)
        email.delete()
        logger.info(f"Deleted email {email.gmail_id} by {request.user.username}")
        return JsonResponse({'message': 'Closed email deleted successfully.'})
    except ClosedEmail.DoesNotExist:
        return JsonResponse({'error': 'Email not found or not authorized.'}, status=404)
    except Exception as e:
        logger.error(f"Error deleting closed email {email_id}: {str(e)}")
        return JsonResponse({'error': 'An error occurred while deleting the email.'}, status=500)

def logout_view(request):
    logout(request)
    return redirect('home')