from django.contrib import admin
from django.utils.html import format_html
from .models import Patient, Visit, PatientDiagnosis, VisitDiagnosis, Diagnosis


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ['pesel', 'full_name', 'age_display', 'gender', 'has_visits', 'has_diabetes_display', 'created_at']
    list_filter = ['gender', 'created_at', 'updated_at']
    search_fields = ['pesel', 'full_name', 'phone_mobile', 'phone_landline']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Dane podstawowe', {
            'fields': ('pesel', 'full_name', 'date_of_birth', 'gender')
        }),
        ('Kontakt', {
            'fields': ('address', 'phone_mobile', 'phone_landline')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    def age_display(self, obj):
        return f"{obj.age} lat"
    age_display.short_description = 'Wiek'
    
    def has_visits(self, obj):
        count = obj.visits.count()
        if count > 0:
            return format_html(
                '<span style="color: green;">✓ {} wizyt</span>',
                count
            )
        return format_html('<span style="color: red;">✗ Brak wizyt</span>')
    has_visits.short_description = 'Wizyty'
    
    def has_diabetes_display(self, obj):
        if obj.has_diabetes():
            return format_html('<span style="color: red;">✓ Tak</span>')
        return format_html('<span style="color: green;">✗ Nie</span>')
    has_diabetes_display.short_description = 'Cukrzyca'


class VisitDiagnosisInline(admin.TabularInline):
    model = VisitDiagnosis
    extra = 1


@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = ['patient_pesel', 'patient_name', 'visit_date', 'quarter', 'has_sbp', 'has_cholesterol', 'has_lab_values']
    list_filter = ['visit_date', 'quarter', 'created_at']
    search_fields = ['patient__pesel', 'patient__full_name']
    readonly_fields = ['quarter', 'created_at']
    date_hierarchy = 'visit_date'
    inlines = [VisitDiagnosisInline]
    
    fieldsets = (
        ('Pacjent i data', {
            'fields': ('patient', 'visit_date', 'quarter')
        }),
        ('Parametry podstawowe', {
            'fields': ('systolic_pressure', 'cholesterol_total', 'cholesterol_hdl')
        }),
        ('Parametry laboratoryjne', {
            'fields': ('hba1c', 'egfr')
        }),
        ('Metadata', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        })
    )
    
    def patient_pesel(self, obj):
        return obj.patient.pesel
    patient_pesel.short_description = 'PESEL'
    
    def patient_name(self, obj):
        return obj.patient.full_name or 'Brak nazwiska'
    patient_name.short_description = 'Pacjent'
    
    def has_sbp(self, obj):
        if obj.systolic_pressure:
            return format_html('<span style="color: green;">✓ {} mmHg</span>', obj.systolic_pressure)
        return format_html('<span style="color: red;">✗</span>')
    has_sbp.short_description = 'Ciśnienie'
    
    def has_cholesterol(self, obj):
        total = obj.cholesterol_total
        hdl = obj.cholesterol_hdl
        if total and hdl:
            return format_html('<span style="color: green;">✓ {}/{}</span>', total, hdl)
        elif total or hdl:
            return format_html('<span style="color: orange;">⚠ Częściowe</span>')
        return format_html('<span style="color: red;">✗</span>')
    has_cholesterol.short_description = 'Cholesterol'
    
    def has_lab_values(self, obj):
        hba1c = obj.hba1c
        egfr = obj.egfr
        if hba1c and egfr:
            return format_html('<span style="color: green;">✓ Pełne</span>')
        elif hba1c or egfr:
            return format_html('<span style="color: orange;">⚠ Częściowe</span>')
        return format_html('<span style="color: red;">✗</span>')
    has_lab_values.short_description = 'Lab'


@admin.register(PatientDiagnosis)
class PatientDiagnosisAdmin(admin.ModelAdmin):
    list_display = ['patient_pesel', 'patient_name', 'diagnosis_code', 'diagnosed_at', 'age_at_diagnosis']
    list_filter = ['diagnosis_code', 'diagnosed_at', 'created_at']
    search_fields = ['patient__pesel', 'patient__full_name', 'diagnosis_code']
    readonly_fields = ['created_at']
    date_hierarchy = 'diagnosed_at'
    
    def patient_pesel(self, obj):
        return obj.patient.pesel
    patient_pesel.short_description = 'PESEL'
    
    def patient_name(self, obj):
        return obj.patient.full_name or 'Brak nazwiska'
    patient_name.short_description = 'Pacjent'


@admin.register(VisitDiagnosis)
class VisitDiagnosisAdmin(admin.ModelAdmin):
    list_display = ['patient_pesel', 'patient_name', 'visit_date', 'diagnosis_code']
    list_filter = ['diagnosis_code', 'visit__visit_date', 'created_at']
    search_fields = ['visit__patient__pesel', 'visit__patient__full_name', 'diagnosis_code']
    readonly_fields = ['created_at']
    
    def patient_pesel(self, obj):
        return obj.visit.patient.pesel
    patient_pesel.short_description = 'PESEL'
    
    def patient_name(self, obj):
        return obj.visit.patient.full_name or 'Brak nazwiska'
    patient_name.short_description = 'Pacjent'
    
    def visit_date(self, obj):
        return obj.visit.visit_date
    visit_date.short_description = 'Data wizyty'


@admin.register(Diagnosis)
class DiagnosisAdmin(admin.ModelAdmin):
    list_display = ['code', 'description', 'usage_count']
    search_fields = ['code', 'description']
    
    def usage_count(self, obj):
        chronic_count = PatientDiagnosis.objects.filter(diagnosis_code=obj.code).count()
        visit_count = VisitDiagnosis.objects.filter(diagnosis_code=obj.code).count()
        total = chronic_count + visit_count
        
        if total > 0:
            return format_html(
                '<span style="color: blue;">{} użyć ({}+{})</span>',
                total, chronic_count, visit_count
            )
        return format_html('<span style="color: gray;">Nieużywane</span>')
    usage_count.short_description = 'Użycie'


# Customize admin site
admin.site.site_header = 'System SCORE2 - Panel administracyjny'
admin.site.site_title = 'System SCORE2'
admin.site.index_title = 'Zarządzanie systemem SCORE2'