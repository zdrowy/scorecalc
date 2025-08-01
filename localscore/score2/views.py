from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.views import View
from django.http import JsonResponse
from django.db import transaction
from django.db.models import Q, Count, Avg, Min, Max
from django.core.paginator import Paginator
from datetime import date
import numpy as np
from typing import Optional, Tuple
from dateutil.relativedelta import relativedelta
import logging

from patients.models import Patient, Visit
from .models import Score2Result

# Configure logger
logger = logging.getLogger('score2')


class CalculateScore2View(View):
    """Calculate SCORE2 for a single patient's latest visit"""
    
    def post(self, request, patient_id):
        patient = get_object_or_404(Patient, pk=patient_id)
        latest_visit = patient.get_latest_visit()
        
        if not latest_visit:
            return JsonResponse({
                'success': False,
                'error': 'Pacjent nie ma żadnych wizyt.'
            })
        
        try:
            result = self._calculate_score_for_visit(patient, latest_visit)
            return JsonResponse({
                'success': True,
                'result': {
                    'score_type': result.score_type,
                    'score_value': str(result.score_value) if result.score_value else None,
                    'risk_level': result.risk_level_display,
                    'is_successful': result.is_calculation_successful,
                    'missing_data': result.missing_data_reason,
                    'notes': result.calculation_notes,
                    'data_source': result.get_data_source_display(),
                }
            })
        except Exception as e:
            logger.error(f"ERROR;{patient.pesel};;;Unexpected error;{str(e)}")
            return JsonResponse({
                'success': False,
                'error': str(e)
            })

    def _calculate_score_for_visit(self, patient: Patient, visit: Visit) -> Score2Result:
        """Calculate appropriate SCORE2 for a patient's visit - ONE result per visit"""
        
        # Remove existing result for this visit
        Score2Result.objects.filter(patient=patient, visit=visit).delete()
        
        # Use visit age for both qualification AND calculation (like original script)
        age_at_visit = patient.calculate_age(visit.visit_date)
        has_diabetes = patient.has_diabetes()
        smoking_status, smoking_info = patient.get_smoking_status()
        smoker = smoking_status == 'smoker'
        
        # Get systolic pressure with fallback logic
        sbp, sbp_info = self._get_systolic_pressure(patient, visit.visit_date)
        
        # Get cholesterol values with fallback logic
        total_chol, hdl_chol, chol_info, chol_source = self._get_cholesterol_values(patient, visit.visit_date)
        
        # Base result object
        result_data = {
            'patient': patient,
            'visit': visit,
            'age_at_calculation': age_at_visit,
            'systolic_pressure': sbp,
            'cholesterol_total': total_chol,
            'cholesterol_hdl': hdl_chol,
            'smoking_status': smoking_status,
            'smoking_info_source': smoking_info,
            'has_diabetes': has_diabetes,
            'cholesterol_info': f"{chol_info}, ciśnienie: {sbp_info}",
            'region': 'high',
        }
        
        # Check if we have systolic pressure (critical for all calculations)
        if sbp is None:
            logger.warning(f"NO_CALC;{patient.pesel};{age_at_visit};;Missing systolic blood pressure;{sbp_info}")
            result_data.update({
                'score_type': '',
                'score_value': None,
                'risk_level': 'not_applicable',
                'is_calculation_successful': False,
                'missing_data_reason': 'Brak ciśnienia skurczowego',
                'calculation_notes': 'Nie można obliczyć bez ciśnienia skurczowego',
                'data_source': 'visit'
            })
            return Score2Result.objects.create(**result_data)
        
        # Use visit age for qualification (same as original script)
        if has_diabetes and age_at_visit >= 40:
            if age_at_visit <= 69:
                logger.info(f"QUALIFYING;{patient.pesel};{age_at_visit};SCORE2-Diabetes;;sbp from {sbp_info}")
                return self._calculate_score2_diabetes(result_data)
            else:  # age 70+
                logger.info(f"QUALIFYING;{patient.pesel};{age_at_visit};SCORE2-OP;diabetic;sbp from {sbp_info}")
                return self._calculate_score2_op(result_data)
        elif not has_diabetes and 40 <= age_at_visit <= 69:
            logger.info(f"QUALIFYING;{patient.pesel};{age_at_visit};SCORE2;;sbp from {sbp_info}")
            return self._calculate_score2(result_data)
        elif 70 <= age_at_visit <= 89:
            logger.info(f"QUALIFYING;{patient.pesel};{age_at_visit};SCORE2-OP;;sbp from {sbp_info}")
            return self._calculate_score2_op(result_data)
        else:
            # Age exclusion based on visit age
            if age_at_visit < 40:
                exclusion_reason = f'Wiek w momencie wizyty {age_at_visit} lat < 40 lat'
                logger.warning(f"EXCLUDED;{patient.pesel};{age_at_visit};;Too young (<40);")
            elif age_at_visit > 89:
                exclusion_reason = f'Wiek w momencie wizyty {age_at_visit} lat > 89 lat'
                logger.warning(f"EXCLUDED;{patient.pesel};{age_at_visit};;Too old (>89);")
            else:
                exclusion_reason = f'Wiek w momencie wizyty {age_at_visit} lat poza zakresem'
                logger.warning(f"EXCLUDED;{patient.pesel};{age_at_visit};;Age out of range;")
            
            result_data.update({
                'score_type': '',
                'score_value': None,
                'risk_level': 'age_out_of_range',
                'is_calculation_successful': False,
                'missing_data_reason': exclusion_reason,
                'calculation_notes': f'Pacjent wykluczony: {exclusion_reason}',
                'data_source': 'visit'
            })
            return Score2Result.objects.create(**result_data)
    def _get_systolic_pressure(self, patient: Patient, visit_date: date) -> Tuple[Optional[int], str]:
        """Get systolic pressure with fallback to previous visits"""
        # First try current visit
        current_visit = patient.visits.filter(visit_date=visit_date).first()
        if current_visit and current_visit.systolic_pressure:
            return current_visit.systolic_pressure, "aktualna wizyta"
        
        # Look for previous visits with systolic pressure
        previous_visits = patient.visits.filter(
            visit_date__lt=visit_date,
            systolic_pressure__isnull=False
        ).order_by('-visit_date')[:5]
        
        for visit in previous_visits:
            if visit.systolic_pressure:
                return visit.systolic_pressure, f"poprzednia wizyta ({visit.visit_date})"
        
        return None, "brak danych"

    def _calculate_score2(self, result_data: dict) -> Score2Result:
        """Calculate SCORE2 for non-diabetic patients aged 40-69"""
        age = result_data['age_at_calculation']
        sbp = result_data['systolic_pressure']
        total_chol = result_data['cholesterol_total']
        hdl_chol = result_data['cholesterol_hdl']
        smoking_status = result_data['smoking_status']
        patient = result_data['patient']
        
        # Check required data
        if total_chol is None or hdl_chol is None:
            missing = []
            if total_chol is None:
                missing.append('cholesterol całkowity')
            if hdl_chol is None:
                missing.append('cholesterol HDL')
            
            logger.warning(f"NO_CALC;{patient.pesel};{age};SCORE2;{', '.join(missing)};")
            
            result_data.update({
                'score_type': 'SCORE2',
                'score_value': None,
                'risk_level': 'not_applicable',
                'is_calculation_successful': False,
                'missing_data_reason': f'Brak danych: {", ".join(missing)}',
                'calculation_notes': f'SCORE2: Brakuje {", ".join(missing)}',
                'data_source': 'visit'
            })
            return Score2Result.objects.create(**result_data)
        
        try:
            smoker = smoking_status == 'smoker'
            score_value = Score2Result.calculate_score2(
                age=age,
                sbp=sbp,
                tchol=total_chol,
                hdl=hdl_chol,
                smoker=smoker,
                sex=patient.gender,
                region=result_data['region']
            )
            
            risk_level = Score2Result.get_risk_level(age, score_value, 'SCORE2')
            
            logger.info(f"SUCCESS;{patient.pesel};{age};SCORE2;{score_value}%;{risk_level}")
            
            result_data.update({
                'score_type': 'SCORE2',
                'score_value': score_value,
                'risk_level': risk_level,
                'is_calculation_successful': True,
                'missing_data_reason': None,
                'calculation_notes': f'SCORE2: sbp={sbp}, tchol={total_chol:.1f}, hdl={hdl_chol:.1f}',
                'data_source': result_data.get('data_source', 'visit')
            })
            
        except Exception as e:
            logger.error(f"ERROR;{patient.pesel};{age};SCORE2;Calculation failed;{str(e)}")
            result_data.update({
                'score_type': 'SCORE2',
                'score_value': None,
                'risk_level': 'not_applicable',
                'is_calculation_successful': False,
                'missing_data_reason': None,
                'calculation_notes': f'Błąd obliczenia SCORE2: {str(e)}',
                'data_source': 'visit'
            })
        
        return Score2Result.objects.create(**result_data)
    
    def _calculate_score2_diabetes(self, result_data: dict) -> Score2Result:
        """Calculate SCORE2-Diabetes for diabetic patients aged 40-69"""
        age = result_data['age_at_calculation']
        sbp = result_data['systolic_pressure']
        patient = result_data['patient']
        visit = result_data['visit']
        
        # Get diabetes-specific data
        age_at_diagnosis = patient.get_diabetes_age_at_diagnosis()
        
        # Get cholesterol values (allow max 1 missing)
        total_chol, hdl_chol, chol_info, chol_source = self._get_cholesterol_values(patient, visit.visit_date)
        missing_chol_count = (total_chol is None) + (hdl_chol is None)
        
        # Get lab values (allow max 1 missing) 
        hba1c, egfr, lab_info, lab_source = self._get_diabetes_lab_values(patient, visit.visit_date)
        missing_lab_count = (egfr is None) + (hba1c is None)
        
        result_data.update({
            'age_at_diabetes_diagnosis': age_at_diagnosis,
            'hba1c': hba1c,
            'egfr': egfr,
            'cholesterol_total': total_chol,
            'cholesterol_hdl': hdl_chol,
        })
        
        # Check required data - same logic as original script
        missing_items = []
        if age_at_diagnosis is None:
            missing_items.append('wiek diagnozy cukrzycy')
        if missing_chol_count > 1:  # More than 1 cholesterol value missing
            missing_items.append('wielokrotne wartości cholesterolu')
        if missing_lab_count > 1:   # More than 1 lab value missing
            missing_items.append('wielokrotne wartości laboratoryjne (eGFR/HbA1c)')
        
        # Key difference: Allow 1 missing from EACH category, not total
        if missing_items:
            logger.warning(f"NO_CALC;{patient.pesel};{age};SCORE2-Diabetes;{', '.join(missing_items)};")
            
            result_data.update({
                'score_type': 'SCORE2-Diabetes',
                'score_value': None,
                'risk_level': 'not_applicable',
                'is_calculation_successful': False,
                'missing_data_reason': f'Brak danych: {", ".join(missing_items)}',
                'calculation_notes': f'SCORE2-Diabetes: Brakuje {", ".join(missing_items)}',
                'data_source': 'mixed' if lab_source != 'visit' or chol_source != 'visit' else 'visit'
            })
            return Score2Result.objects.create(**result_data)
        
        try:
            smoking_status = result_data['smoking_status']
            smoker = smoking_status == 'smoker'
            
            score_value = Score2Result.calculate_score2_diabetes(
                age=age,
                sbp=sbp,
                tchol=total_chol,
                hdl=hdl_chol,
                smoker=smoker,
                diabetes=True,
                age_at_diagnosis=age_at_diagnosis,
                a1c=hba1c,
                egfr=egfr,
                sex=patient.gender,
                region=result_data['region']
            )
            
            risk_level = Score2Result.get_risk_level(age, score_value, 'SCORE2-Diabetes')
            
            logger.info(f"SUCCESS;{patient.pesel};{age};SCORE2-Diabetes;{score_value}%;{risk_level}")
            
            # Determine data source
            data_source = 'visit'
            if lab_source != 'visit' or chol_source != 'visit':
                data_source = 'mixed'
            elif lab_source == 'median' or chol_source == 'median':
                data_source = 'median'
            
            result_data.update({
                'score_type': 'SCORE2-Diabetes',
                'score_value': score_value,
                'risk_level': risk_level,
                'is_calculation_successful': True,
                'missing_data_reason': None,
                'calculation_notes': (
                    f'SCORE2-Diabetes: sbp={sbp}, tchol={total_chol:.1f}, hdl={hdl_chol:.1f}, '
                    f'egfr={egfr:.1f}, hba1c={hba1c:.1f}, wiek_dx={age_at_diagnosis}'
                ),
                'data_source': data_source
            })
            
        except Exception as e:
            logger.error(f"ERROR;{patient.pesel};{age};SCORE2-Diabetes;Calculation failed;{str(e)}")
            result_data.update({
                'score_type': 'SCORE2-Diabetes',
                'score_value': None,
                'risk_level': 'not_applicable',
                'is_calculation_successful': False,
                'missing_data_reason': None,
                'calculation_notes': f'Błąd obliczenia SCORE2-Diabetes: {str(e)}',
                'data_source': 'mixed' if lab_source != 'visit' or chol_source != 'visit' else 'visit'
            })
        
        return Score2Result.objects.create(**result_data)
    
    def _calculate_score2_op(self, result_data: dict) -> Score2Result:
        """Calculate SCORE2-OP for patients aged 70-89"""
        age = result_data['age_at_calculation']
        sbp = result_data['systolic_pressure']
        total_chol = result_data['cholesterol_total']
        hdl_chol = result_data['cholesterol_hdl']
        smoking_status = result_data['smoking_status']
        has_diabetes = result_data['has_diabetes']
        patient = result_data['patient']
        
        # Check required data
        if total_chol is None or hdl_chol is None:
            missing = []
            if total_chol is None:
                missing.append('cholesterol całkowity')
            if hdl_chol is None:
                missing.append('cholesterol HDL')
            
            logger.warning(f"NO_CALC;{patient.pesel};{age};SCORE2-OP;{', '.join(missing)};")
            
            result_data.update({
                'score_type': 'SCORE2-OP',
                'score_value': None,
                'risk_level': 'not_applicable',
                'is_calculation_successful': False,
                'missing_data_reason': f'Brak danych: {", ".join(missing)}',
                'calculation_notes': f'SCORE2-OP: Brakuje {", ".join(missing)}',
                'data_source': 'visit'
            })
            return Score2Result.objects.create(**result_data)
        
        try:
            smoker = smoking_status == 'smoker'
            score_value = Score2Result.calculate_score2_op(
                age=age,
                sbp=sbp,
                tchol=total_chol,
                hdl=hdl_chol,
                smoker=smoker,
                diabetes=has_diabetes,
                sex=patient.gender,
                region='moderate'  # SCORE2-OP uses moderate as default
            )
            
            risk_level = Score2Result.get_risk_level(age, score_value, 'SCORE2-OP')
            
            logger.info(f"SUCCESS;{patient.pesel};{age};SCORE2-OP;{score_value}%;{risk_level}")
            
            result_data.update({
                'score_type': 'SCORE2-OP',
                'score_value': score_value,
                'risk_level': risk_level,
                'is_calculation_successful': True,
                'missing_data_reason': None,
                'calculation_notes': (
                    f'SCORE2-OP: sbp={sbp}, tchol={total_chol:.1f}, hdl={hdl_chol:.1f}, '
                    f'diabetes={has_diabetes}'
                ),
                'data_source': result_data.get('data_source', 'visit')
            })
            
        except Exception as e:
            logger.error(f"ERROR;{patient.pesel};{age};SCORE2-OP;Calculation failed;{str(e)}")
            result_data.update({
                'score_type': 'SCORE2-OP',
                'score_value': None,
                'risk_level': 'not_applicable',
                'is_calculation_successful': False,
                'missing_data_reason': None,
                'calculation_notes': f'Błąd obliczenia SCORE2-OP: {str(e)}',
                'data_source': 'visit'
            })
        
        return Score2Result.objects.create(**result_data)
    
    def _get_cholesterol_values(self, patient: Patient, visit_date: date) -> Tuple[Optional[float], Optional[float], str, str]:
        """Get cholesterol values with fallback to previous visits and median - returns data source"""
        # First try current visit
        current_visit = patient.visits.filter(visit_date=visit_date).first()
        if current_visit and current_visit.cholesterol_total and current_visit.cholesterol_hdl:
            return (
                float(current_visit.cholesterol_total),
                float(current_visit.cholesterol_hdl),
                "aktualna wizyta",
                'visit'
            )
        
        # Look for previous visits
        previous_visits = patient.visits.filter(
            visit_date__lt=visit_date,
            cholesterol_total__isnull=False,
            cholesterol_hdl__isnull=False
        ).order_by('-visit_date')[:5]
        
        for visit in previous_visits:
            if visit.cholesterol_total and visit.cholesterol_hdl:
                return (
                    float(visit.cholesterol_total),
                    float(visit.cholesterol_hdl),
                    f"poprzednia wizyta ({visit.visit_date})",
                    'previous_visit'
                )
        
        # Try to complete partial data with median
        total_chol = None
        hdl_chol = None
        info_parts = []
        data_source = 'visit'
        
        if current_visit:
            total_chol = float(current_visit.cholesterol_total) if current_visit.cholesterol_total else None
            hdl_chol = float(current_visit.cholesterol_hdl) if current_visit.cholesterol_hdl else None
        
        # Fill missing values from previous visits
        if not total_chol or not hdl_chol:
            for visit in previous_visits:
                if not total_chol and visit.cholesterol_total:
                    total_chol = float(visit.cholesterol_total)
                    data_source = 'mixed'
                if not hdl_chol and visit.cholesterol_hdl:
                    hdl_chol = float(visit.cholesterol_hdl)
                    data_source = 'mixed'
                if total_chol and hdl_chol:
                    break
        
        # Use median for missing values (only if max 1 missing)
        missing_count = (total_chol is None) + (hdl_chol is None)
        if missing_count <= 1:
            age = patient.calculate_age(visit_date)
            age_group_start = (age // 10) * 10
            age_group_end = age_group_start + 9
            
            if total_chol is None:
                total_chol = self._get_median_value(age_group_start, age_group_end, 'total')
                info_parts.append(f"cholesterol_całkowity_mediana_{age_group_start}-{age_group_end}")
                data_source = 'median'
            
            if hdl_chol is None:
                hdl_chol = self._get_median_value(age_group_start, age_group_end, 'hdl')
                info_parts.append(f"cholesterol_HDL_mediana_{age_group_start}-{age_group_end}")
                data_source = 'median'
        
        if info_parts:
            return total_chol, hdl_chol, f"uzupełnione medianą: {', '.join(info_parts)}", data_source
        
        return total_chol, hdl_chol, "niepełne dane", data_source
    
    def _get_diabetes_lab_values(self, patient: Patient, visit_date: date) -> Tuple[Optional[float], Optional[float], str, str]:
        """Get eGFR and HbA1c values with fallback logic - returns data source"""
        # First try current visit
        current_visit = patient.visits.filter(visit_date=visit_date).first()
        if current_visit and current_visit.egfr and current_visit.hba1c:
            return (
                float(current_visit.hba1c),
                float(current_visit.egfr),
                "aktualna wizyta",
                'visit'
            )
        
        # Look for previous visits
        previous_visits = patient.visits.filter(
            visit_date__lt=visit_date,
            egfr__isnull=False,
            hba1c__isnull=False
        ).order_by('-visit_date')[:5]
        
        for visit in previous_visits:
            if visit.egfr and visit.hba1c:
                return (
                    float(visit.hba1c),
                    float(visit.egfr),
                    f"poprzednia wizyta ({visit.visit_date})",
                    'previous_visit'
                )
        
        # Try to complete partial data
        egfr = None
        hba1c = None
        info_parts = []
        data_source = 'visit'
        
        if current_visit:
            egfr = float(current_visit.egfr) if current_visit.egfr else None
            hba1c = float(current_visit.hba1c) if current_visit.hba1c else None
        
        # Fill missing values from previous visits
        if not egfr or not hba1c:
            for visit in previous_visits:
                if not egfr and visit.egfr:
                    egfr = float(visit.egfr)
                    data_source = 'mixed'
                if not hba1c and visit.hba1c:
                    hba1c = float(visit.hba1c)
                    data_source = 'mixed'
                if egfr and hba1c:
                    break
        
        # Use median for missing values (only if max 1 missing)
        missing_count = (egfr is None) + (hba1c is None)
        if missing_count <= 1:
            age = patient.calculate_age(visit_date)
            age_group_start = (age // 10) * 10
            age_group_end = age_group_start + 9
            
            if egfr is None:
                egfr = self._get_median_value(age_group_start, age_group_end, 'egfr')
                info_parts.append(f"eGFR_mediana_{age_group_start}-{age_group_end}")
                data_source = 'median'
            
            if hba1c is None:
                hba1c = self._get_median_value(age_group_start, age_group_end, 'hba1c')
                info_parts.append(f"HbA1c_mediana_{age_group_start}-{age_group_end}")
                data_source = 'median'
        
        if info_parts:
            return hba1c, egfr, f"uzupełnione medianą: {', '.join(info_parts)}", data_source
        
        return hba1c, egfr, "niepełne dane", data_source
    
    def _get_median_value(self, age_start: int, age_end: int, value_type: str) -> Optional[float]:
        """Calculate median value for age group"""
        from django.db.models import Q
        from datetime import date, timedelta
        
        # Map value types to model fields
        field_mapping = {
            'total': 'cholesterol_total',
            'hdl': 'cholesterol_hdl',
            'egfr': 'egfr',
            'hba1c': 'hba1c'
        }
        
        field_name = field_mapping.get(value_type)
        if not field_name:
            return None
        
        # Calculate date range for age group
        today = date.today()
        end_date = today - timedelta(days=age_start * 365.25)
        start_date = today - timedelta(days=(age_end + 1) * 365.25)
        
        # Get values from visits
        visits = Visit.objects.filter(
            patient__date_of_birth__gte=start_date,
            patient__date_of_birth__lt=end_date
        ).exclude(**{f"{field_name}__isnull": True})
        
        values = list(visits.values_list(field_name, flat=True))
        
        if not values:
            return None
        
        return float(np.median([float(v) for v in values]))


class CalculateAllScore2View(View):
    """Calculate SCORE2 for all patients"""
    
    def post(self, request):
        try:
            results = self._calculate_scores_for_all()
            return JsonResponse({
                'success': True,
                'results': results
            })
        except Exception as e:
            logger.error(f"ERROR;;;CalculateAllScore2View;{str(e)};")
            return JsonResponse({
                'success': False,
                'error': str(e)
            })
    
    def _calculate_scores_for_all(self):
        """Calculate scores for all eligible patients"""
        # Get all patients with visits in score-eligible age range (40-89) - use CURRENT age
        today = date.today()
        min_date = today - relativedelta(years=90)
        max_date = today - relativedelta(years=40)
        
        patients = Patient.objects.filter(
            visits__isnull=False,
            date_of_birth__gt=min_date,
            date_of_birth__lte=max_date
        ).distinct()
        
        total_processed = 0
        successful_calculations = 0
        failed_calculations = 0
        excluded_patients = 0
        
        logger.info(f"BATCH_START;;;Starting batch calculation;{patients.count()} patients;")
        
        calculator = CalculateScore2View()
        
        with transaction.atomic():
            for patient in patients:
                latest_visit = patient.get_latest_visit()
                if not latest_visit:
                    logger.warning(f"NO_VISIT;{patient.pesel};;;No visits found;")
                    continue
                
                try:
                    result = calculator._calculate_score_for_visit(patient, latest_visit)
                    total_processed += 1
                    
                    if result.is_calculation_successful:
                        successful_calculations += 1
                    else:
                        if result.missing_data_reason:
                            failed_calculations += 1
                        else:
                            excluded_patients += 1
                            
                except Exception as e:
                    logger.error(f"ERROR;{patient.pesel};;;Processing error;{str(e)}")
                    failed_calculations += 1
                    continue
        
        logger.info(f"BATCH_END;;;Processed: {total_processed};Success: {successful_calculations} Failed: {failed_calculations} Excluded: {excluded_patients};")
        
        return {
            'total_processed': total_processed,
            'successful_calculations': successful_calculations,
            'failed_calculations': failed_calculations,
            'excluded_patients': excluded_patients,
        }


class Score2StatsView(View):
    """View for SCORE2 statistics and dashboard"""
    
    def get(self, request):
        # Overall statistics - only for score-eligible patients
        today = date.today()
        min_date = today - relativedelta(years=90)
        max_date = today - relativedelta(years=40)
        
        eligible_patients = Patient.objects.filter(
            date_of_birth__gt=min_date,
            date_of_birth__lte=max_date
        )
        
        total_patients = Patient.objects.count()
        eligible_count = eligible_patients.count()
        patients_with_visits = eligible_patients.filter(visits__isnull=False).distinct().count()
        patients_with_scores = eligible_patients.filter(
            score2_results__is_calculation_successful=True
        ).distinct().count()
        
        # Score type distribution
        score_type_stats = {}
        for score_type, _ in Score2Result.SCORE_TYPE_CHOICES:
            count = Score2Result.objects.filter(
                score_type=score_type,
                is_calculation_successful=True
            ).count()
            if count > 0:
                avg_score = Score2Result.objects.filter(
                    score_type=score_type,
                    is_calculation_successful=True
                ).aggregate(Avg('score_value'))['score_value__avg']
                
                score_type_stats[score_type] = {
                    'count': count,
                    'avg_score': round(avg_score, 2) if avg_score else 0
                }
        
        # Risk level distribution
        risk_stats = {}
        total_successful = Score2Result.objects.filter(is_calculation_successful=True).count()
        
        for risk_level, display_name in Score2Result.RISK_LEVEL_CHOICES:
            count = Score2Result.objects.filter(
                risk_level=risk_level,
                is_calculation_successful=True
            ).count()
            if count > 0:
                risk_stats[risk_level] = {
                    'count': count,
                    'display_name': display_name,
                    'percentage': round((count / total_successful * 100), 1) if total_successful > 0 else 0
                }
        
        # Failed calculations
        failed_calculations = Score2Result.objects.filter(
            is_calculation_successful=False
        ).count()
        
        context = {
            'total_patients': total_patients,
            'eligible_patients': eligible_count,
            'patients_with_visits': patients_with_visits,
            'patients_with_scores': patients_with_scores,
            'score_type_stats': score_type_stats,
            'risk_stats': risk_stats,
            'failed_calculations': failed_calculations,
        }
        
        return render(request, 'score2/stats.html', context)