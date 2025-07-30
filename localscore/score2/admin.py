from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Count, Avg
from .models import Score2Result


@admin.register(Score2Result)
class Score2ResultAdmin(admin.ModelAdmin):
    list_display = [
        'patient_pesel', 'patient_name', 'visit_date', 'score_type', 
        'score_display', 'risk_level_display', 'is_successful', 'created_at'
    ]
    list_filter = [
        'score_type', 'risk_level', 'is_calculation_successful', 
        'has_diabetes', 'region', 'created_at'
    ]
    search_fields = ['patient__pesel', 'patient__full_name']
    readonly_fields = [
        'created_at', 'updated_at', 'age_at_calculation',
        'smoking_status', 'smoking_info_source', 'cholesterol_info'
    ]
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('Pacjent i wizyta', {
            'fields': ('patient', 'visit', 'age_at_calculation', 'created_at')
        }),
        ('Wynik SCORE2', {
            'fields': ('score_type', 'score_value', 'risk_level', 'region', 'is_calculation_successful')
        }),
        ('Parametry wejściowe - podstawowe', {
            'fields': ('systolic_pressure', 'cholesterol_total', 'cholesterol_hdl')
        }),
        ('Status palenia', {
            'fields': ('smoking_status', 'smoking_info_source'),
            'classes': ('collapse',)
        }),
        ('Parametry cukrzycy', {
            'fields': ('has_diabetes', 'age_at_diabetes_diagnosis', 'hba1c', 'egfr'),
            'classes': ('collapse',)
        }),
        ('Informacje dodatkowe', {
            'fields': ('cholesterol_info', 'calculation_notes', 'missing_data_reason'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('updated_at',),
            'classes': ('collapse',)
        })
    )
    
    def patient_pesel(self, obj):
        return obj.patient.pesel
    patient_pesel.short_description = 'PESEL'
    patient_pesel.admin_order_field = 'patient__pesel'
    
    def patient_name(self, obj):
        return obj.patient.full_name or 'Brak nazwiska'
    patient_name.short_description = 'Pacjent'
    patient_name.admin_order_field = 'patient__full_name'
    
    def visit_date(self, obj):
        return obj.visit.visit_date
    visit_date.short_description = 'Data wizyty'
    visit_date.admin_order_field = 'visit__visit_date'
    
    def score_display(self, obj):
        if obj.is_calculation_successful and obj.score_value is not None:
            color = 'green'
            if obj.risk_level == 'high':
                color = 'orange'
            elif obj.risk_level == 'very_high':
                color = 'red'
            
            return format_html(
                '<span style="color: {}; font-weight: bold;">{}%</span>',
                color, obj.score_value
            )
        elif obj.missing_data_reason:
            return format_html(
                '<span style="color: red;" title="{}">Brak danych</span>',
                obj.missing_data_reason
            )
        else:
            return format_html('<span style="color: red;">Błąd</span>')
    score_display.short_description = 'Wynik SCORE2'
    
    def risk_level_display(self, obj):
        colors = {
            'low_to_moderate': 'green',
            'high': 'orange', 
            'very_high': 'red',
            'not_applicable': 'gray',
            'age_out_of_range': 'gray'
        }
        color = colors.get(obj.risk_level, 'gray')
        display_name = obj.get_risk_level_display()
        
        return format_html(
            '<span style="color: {};">{}</span>',
            color, display_name
        )
    risk_level_display.short_description = 'Poziom ryzyka'
    
    def is_successful(self, obj):
        if obj.is_calculation_successful:
            return format_html('<span style="color: green;">✓ Tak</span>')
        return format_html('<span style="color: red;">✗ Nie</span>')
    is_successful.short_description = 'Sukces'
    is_successful.admin_order_field = 'is_calculation_successful'
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('patient', 'visit')
    
    def changelist_view(self, request, extra_context=None):
        # Add summary statistics to changelist
        response = super().changelist_view(request, extra_context=extra_context)
        
        try:
            qs = response.context_data['cl'].queryset
            
            # Calculate statistics
            total_results = qs.count()
            successful_results = qs.filter(is_calculation_successful=True).count()
            
            # Score type distribution
            score_types = qs.values('score_type').annotate(
                count=Count('score_type'),
                avg_score=Avg('score_value')
            ).order_by('score_type')
            
            # Risk level distribution
            risk_levels = qs.filter(is_calculation_successful=True).values('risk_level').annotate(
                count=Count('risk_level')
            ).order_by('risk_level')
            
            response.context_data.update({
                'summary': {
                    'total_results': total_results,
                    'successful_results': successful_results,
                    'success_rate': (successful_results / total_results * 100) if total_results > 0 else 0,
                    'score_types': score_types,
                    'risk_levels': risk_levels,
                }
            })
        except (AttributeError, KeyError):
            pass
        
        return response
    
    # Custom actions
    actions = ['recalculate_selected']
    
    def recalculate_selected(self, request, queryset):
        """Recalculate SCORE2 for selected results"""
        from score2.views import CalculateAllScore2View
        
        calculator = CalculateAllScore2View()
        updated_count = 0
        
        for result in queryset:
            try:
                new_result = calculator._calculate_score_for_visit(
                    result.patient, 
                    result.visit
                )
                updated_count += 1
            except Exception as e:
                self.message_user(
                    request,
                    f'Błąd przeliczania dla pacjenta {result.patient.pesel}: {str(e)}',
                    level='ERROR'
                )
        
        if updated_count > 0:
            self.message_user(
                request,
                f'Pomyślnie przeliczono SCORE2 dla {updated_count} pacjentów.',
                level='SUCCESS'
            )
    
    recalculate_selected.short_description = 'Przelicz SCORE2 dla wybranych'