from django.db.models import Q, Count, Prefetch, Max, Avg
from django.shortcuts import render, get_object_or_404, redirect
from .forms import VisitForm, VisitEditForm, PatientSmokingForm
from django.contrib import messages
from django.views.generic import ListView, DetailView
from django.views import View
from django.http import JsonResponse
from django.core.paginator import Paginator
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
import pandas as pd
import numpy as np
import unicodedata
import re
import pathlib

from .models import Patient, Visit, PatientDiagnosis, VisitDiagnosis, Diagnosis
from .forms import VisitForm, PatientSmokingForm
from score2.models import Score2Result

class PatientListView(ListView):
    model = Patient
    template_name = 'patients/patient_list.html'
    context_object_name = 'patients'
    paginate_by = 20
        
    def get_queryset(self):
        queryset = Patient.objects.select_related().prefetch_related(
            'visits',
            Prefetch('score2_results', 
                    queryset=Score2Result.objects.order_by('-created_at')),
        ).annotate(
            visits_count=Count('visits'),
            score2_count=Count('score2_results'),
            latest_score=Max('score2_results__score_value')
        )
        
        # Domyślnie tylko pacjenci w wieku 40-89 lat (kwalifikowalni do SCORE2)
        age_filter = self.request.GET.get('age', 'score_eligible')
        if age_filter == 'score_eligible':
            today = date.today()
            min_date = today - relativedelta(years=90)  # max 89 lat
            max_date = today - relativedelta(years=40)  # min 40 lat
            queryset = queryset.filter(
                date_of_birth__gt=min_date,
                date_of_birth__lte=max_date
            )
        elif age_filter == 'all':
            pass  # Pokaż wszystkich
        elif age_filter == '40-49':
            today = date.today()
            start_date = today - relativedelta(years=50)
            end_date = today - relativedelta(years=40)
            queryset = queryset.filter(
                date_of_birth__gt=start_date,
                date_of_birth__lte=end_date
            )
        elif age_filter == '50-59':
            today = date.today()
            start_date = today - relativedelta(years=60)
            end_date = today - relativedelta(years=50)
            queryset = queryset.filter(
                date_of_birth__gt=start_date,
                date_of_birth__lte=end_date
            )
        elif age_filter == '60-69':
            today = date.today()
            start_date = today - relativedelta(years=70)
            end_date = today - relativedelta(years=60)
            queryset = queryset.filter(
                date_of_birth__gt=start_date,
                date_of_birth__lte=end_date
            )
        elif age_filter == '70+':
            today = date.today()
            end_date = today - relativedelta(years=70)
            queryset = queryset.filter(date_of_birth__lte=end_date)
        
        # Search functionality
        search_query = self.request.GET.get('search')
        if search_query:
            queryset = queryset.filter(
                Q(pesel__icontains=search_query) |
                Q(full_name__icontains=search_query)
            )
        
        # Filter by risk level instead of diabetes
        risk_filter = self.request.GET.get('risk_level')
        if risk_filter and risk_filter != 'all':
            queryset = queryset.filter(
                score2_results__risk_level=risk_filter,
                score2_results__is_calculation_successful=True
            ).distinct()
        
        # Filter by SCORE2 calculation status
        score_filter = self.request.GET.get('score_status')
        if score_filter == 'calculated':
            queryset = queryset.filter(
                score2_results__is_calculation_successful=True
            ).distinct()
        elif score_filter == 'not_calculated':
            queryset = queryset.filter(
                Q(score2_results__isnull=True) |
                Q(score2_results__is_calculation_successful=False)
            ).distinct()
        
        # Sortowanie: od największego score do najmniejszego, potem najnowsze
        return queryset.order_by('-latest_score', '-updated_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Add summary statistics
        all_patients = Patient.objects.all()
        total_patients = all_patients.count()
        
        # Eligible patients (40-89 years)
        today = date.today()
        min_date = today - relativedelta(years=90)
        max_date = today - relativedelta(years=40)
        eligible_patients = all_patients.filter(
            date_of_birth__gt=min_date,
            date_of_birth__lte=max_date
        )
        eligible_count = eligible_patients.count()
        
        patients_with_visits = eligible_patients.filter(visits__isnull=False).distinct().count()
        patients_with_scores = eligible_patients.filter(
            score2_results__is_calculation_successful=True
        ).distinct().count()
        
        # Risk level statistics instead of diabetes
        risk_level_stats = {}
        from score2.models import Score2Result
        
        for risk_level, display_name in Score2Result.RISK_LEVEL_CHOICES:
            if risk_level != 'not_applicable' and risk_level != 'age_out_of_range':
                count = eligible_patients.filter(
                    score2_results__risk_level=risk_level,
                    score2_results__is_calculation_successful=True
                ).distinct().count()
                risk_level_stats[risk_level] = {
                    'count': count,
                    'display_name': display_name
                }
        
        context.update({
            'total_patients': total_patients,
            'eligible_patients': eligible_count,
            'patients_with_visits': patients_with_visits,
            'patients_with_scores': patients_with_scores,
            'risk_level_stats': risk_level_stats,
            'search_query': self.request.GET.get('search', ''),
            'risk_filter': self.request.GET.get('risk_level', ''),
            'age_filter': self.request.GET.get('age', 'score_eligible'),
            'score_filter': self.request.GET.get('score_status', ''),
        })
        
        return context


class PatientDetailView(DetailView):
    model = Patient
    template_name = 'patients/patient_detail.html'
    context_object_name = 'patient'
    
    def get_object(self):
        return get_object_or_404(
            Patient.objects.prefetch_related(
                'visits__diagnoses',
                'chronic_diagnoses',
                Prefetch(
                    'score2_results',
                    queryset=Score2Result.objects.select_related('visit').order_by('-created_at')
                )
            ),
            pk=self.kwargs['pk']
        )
    
    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        
        # Update visit
        if 'update_visit' in request.POST:
            visit_id = request.POST.get('visit_id')
            visit = get_object_or_404(Visit, id=visit_id, patient=self.object)
            form = VisitEditForm(request.POST, instance=visit)  # Use VisitEditForm instead
            if form.is_valid():
                try:
                    form.save()
                    messages.success(request, 'Wizyta została zaktualizowana.')
                    
                    # Usuń stary wynik SCORE2 dla tej wizyty
                    Score2Result.objects.filter(patient=self.object, visit=visit).delete()
                    
                    return redirect('patients:patient_detail', pk=self.object.pk)
                except ValidationError as e:
                    messages.error(request, f'Błąd: {e.message}')
            else:
                # Add detailed form errors for debugging
                error_messages = []
                for field, errors in form.errors.items():
                    error_messages.append(f'{field}: {", ".join(errors)}')
                messages.error(request, f'Błąd walidacji formularza: {"; ".join(error_messages)}')
        
        # Update visit and calculate SCORE2
        elif 'update_visit_and_calculate' in request.POST:
            visit_id = request.POST.get('visit_id')
            visit = get_object_or_404(Visit, id=visit_id, patient=self.object)
            form = VisitEditForm(request.POST, instance=visit)  # Use VisitEditForm instead
            if form.is_valid():
                try:
                    form.save()
                    
                    # Calculate SCORE2 for this visit
                    from score2.views import CalculateScore2View
                    calculator = CalculateScore2View()
                    result = calculator._calculate_score_for_visit(self.object, visit)
                    
                    if result.is_calculation_successful:
                        messages.success(
                            request, 
                            f'Wizyta zaktualizowana i SCORE2 obliczone: {result.score_type} = {result.score_value}%'
                        )
                    else:
                        messages.warning(
                            request, 
                            f'Wizyta zaktualizowana, ale nie można obliczyć SCORE2: {result.missing_data_reason}'
                        )
                    
                    return redirect('patients:patient_detail', pk=self.object.pk)
                except ValidationError as e:
                    messages.error(request, f'Błąd: {e.message}')
                except Exception as e:
                    messages.error(request, f'Błąd podczas obliczania: {str(e)}')
            else:
                # Add detailed form errors for debugging
                error_messages = []
                for field, errors in form.errors.items():
                    error_messages.append(f'{field}: {", ".join(errors)}')
                messages.error(request, f'Błąd walidacji formularza: {"; ".join(error_messages)}')
        
        # Calculate SCORE2 for specific visit
        elif 'calculate_visit_score' in request.POST:
            visit_id = request.POST.get('visit_id')
            visit = get_object_or_404(Visit, id=visit_id, patient=self.object)
            
            try:
                from score2.views import CalculateScore2View
                calculator = CalculateScore2View()
                result = calculator._calculate_score_for_visit(self.object, visit)
                
                if result.is_calculation_successful:
                    messages.success(
                        request, 
                        f'SCORE2 obliczone dla wizyty {visit.visit_date}: {result.score_type} = {result.score_value}%'
                    )
                else:
                    messages.warning(
                        request, 
                        f'Nie można obliczyć SCORE2 dla wizyty {visit.visit_date}: {result.missing_data_reason}'
                    )
            except Exception as e:
                messages.error(request, f'Błąd podczas obliczania: {str(e)}')
            
            return redirect('patients:patient_detail', pk=self.object.pk)
        
        # Update smoking status
        elif 'update_smoking' in request.POST:
            form = PatientSmokingForm(request.POST, instance=self.object)
            if form.is_valid():
                form.save()
                messages.success(request, 'Status palenia został zaktualizowany.')
                
                # Usuń wszystkie wyniki SCORE2 - będą przeliczone z nowym statusem palenia
                Score2Result.objects.filter(patient=self.object).delete()
                
                return redirect('patients:patient_detail', pk=self.object.pk)
        
        # Recalculate SCORE2
        elif 'recalculate_score' in request.POST:
            from score2.views import CalculateScore2View
            calculator = CalculateScore2View()
            latest_visit = self.object.get_latest_visit()
            
            if not latest_visit:
                messages.error(request, 'Brak wizyt dla tego pacjenta.')
                return redirect('patients:patient_detail', pk=self.object.pk)
            
            try:
                result = calculator._calculate_score_for_visit(self.object, latest_visit)
                if result.is_calculation_successful:
                    messages.success(
                        request, 
                        f'SCORE2 obliczone pomyślnie: {result.score_type} = {result.score_value}%'
                    )
                else:
                    messages.warning(
                        request, 
                        f'Nie można obliczyć SCORE2: {result.missing_data_reason}'
                    )
            except Exception as e:
                messages.error(request, f'Błąd podczas obliczania: {str(e)}')
            
            return redirect('patients:patient_detail', pk=self.object.pk)
        
        # Add new visit
        elif 'add_visit' in request.POST:
            form = VisitForm(request.POST)
            if form.is_valid():
                try:
                    visit = form.save(commit=False)
                    visit.patient = self.object
                    visit.save()
                    messages.success(request, 'Nowa wizyta została dodana.')
                    return redirect('patients:patient_detail', pk=self.object.pk)
                except ValidationError as e:
                    messages.error(request, f'Błąd: {e.message}')
            else:
                messages.error(request, 'Błąd walidacji formularza.')
        
        return self.get(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        patient = self.object
        
        # Get visits ordered by date
        visits = patient.visits.all().order_by('-visit_date')
        
        # Get SCORE2 results grouped by visit
        score_results = {}
        for result in patient.score2_results.all():
            visit_id = result.visit.id
            score_results[visit_id] = result
        
        # Prepare visit data with score results
        visit_data = []
        for visit in visits:
            score_result = score_results.get(visit.id)
            visit_data.append({
                'visit': visit,
                'score': score_result,
                'has_score': score_result is not None,
                'age': patient.calculate_age(visit.visit_date),
            })
        
        # Get smoking status
        smoking_status, smoking_info = patient.get_smoking_status()
        
        # Get diabetes info
        has_diabetes = patient.has_diabetes()
        diabetes_age = patient.get_diabetes_age_at_diagnosis()
        
        # SCORE2 results analysis
        successful_scores = patient.score2_results.filter(is_calculation_successful=True).order_by('created_at')
        score_stats = None
        
        if successful_scores.count() > 1:
            first_score = successful_scores.first()
            latest_score = successful_scores.last()
            
            if first_score.score_value and latest_score.score_value:
                trend = float(latest_score.score_value) - float(first_score.score_value)
                score_stats = {
                    'count': successful_scores.count(),
                    'first_score': first_score,
                    'latest_score': latest_score,
                    'trend': trend,
                    'trend_direction': 'up' if trend > 0 else 'down' if trend < 0 else 'stable',
                    'trend_percentage': abs(trend),
                    'average_score': successful_scores.aggregate(Avg('score_value'))['score_value__avg']
                }
        
        # Calculate what scores are possible for latest visit
        latest_visit = patient.get_latest_visit()
        possible_scores = []
        missing_data = []
        data_sources = {}
        
        if latest_visit:
            age = patient.calculate_age(latest_visit.visit_date)
            
            # Check data availability and sources for each score type
            if 40 <= age <= 69:
                # SCORE2 requirements
                missing_score2 = []
                sources_score2 = {}
                
                # Systolic pressure
                if latest_visit.systolic_pressure:
                    sources_score2['systolic_pressure'] = f"{latest_visit.systolic_pressure} mmHg (wizyta)"
                else:
                    missing_score2.append('ciśnienie skurczowe')
                
                # Cholesterol
                if latest_visit.cholesterol_total and latest_visit.cholesterol_hdl:
                    sources_score2['cholesterol'] = f"TC: {latest_visit.cholesterol_total}, HDL: {latest_visit.cholesterol_hdl} (wizyta)"
                elif latest_visit.cholesterol_total:
                    missing_score2.append('cholesterol HDL')
                    sources_score2['cholesterol'] = f"TC: {latest_visit.cholesterol_total} (wizyta), brak HDL"
                elif latest_visit.cholesterol_hdl:
                    missing_score2.append('cholesterol całkowity')
                    sources_score2['cholesterol'] = f"HDL: {latest_visit.cholesterol_hdl} (wizyta), brak TC"
                else:
                    missing_score2.append('cholesterol całkowity i HDL')
                
                # Smoking status source
                sources_score2['smoking'] = f"{smoking_status} ({smoking_info})"
                
                if not missing_score2:
                    possible_scores.append('SCORE2')
                data_sources['SCORE2'] = {'missing': missing_score2, 'sources': sources_score2}
                
                # SCORE2-Diabetes requirements (if has diabetes)
                if has_diabetes:
                    missing_diabetes = missing_score2.copy()  # Start with SCORE2 requirements
                    sources_diabetes = sources_score2.copy()
                    
                    # Additional diabetes requirements
                    if latest_visit.hba1c:
                        sources_diabetes['hba1c'] = f"{latest_visit.hba1c}% (wizyta)"
                    else:
                        missing_diabetes.append('hemoglobina glikowana (HbA1c)')
                    
                    if latest_visit.egfr:
                        sources_diabetes['egfr'] = f"{latest_visit.egfr} ml/min/1.73m² (wizyta)"
                    else:
                        missing_diabetes.append('eGFR')
                    
                    if diabetes_age:
                        sources_diabetes['diabetes_age'] = f"{diabetes_age} lat (diagnoza)"
                    else:
                        missing_diabetes.append('wiek przy diagnozie cukrzycy')
                    
                    if not missing_diabetes:
                        possible_scores.append('SCORE2-Diabetes')
                    data_sources['SCORE2-Diabetes'] = {'missing': missing_diabetes, 'sources': sources_diabetes}
            
            elif age >= 70:
                # SCORE2-OP requirements
                missing_op = []
                sources_op = {}
                
                if latest_visit.systolic_pressure:
                    sources_op['systolic_pressure'] = f"{latest_visit.systolic_pressure} mmHg (wizyta)"
                else:
                    missing_op.append('ciśnienie skurczowe')
                
                if latest_visit.cholesterol_total and latest_visit.cholesterol_hdl:
                    sources_op['cholesterol'] = f"TC: {latest_visit.cholesterol_total}, HDL: {latest_visit.cholesterol_hdl} (wizyta)"
                else:
                    missing_chol = []
                    if not latest_visit.cholesterol_total:
                        missing_chol.append('cholesterol całkowity')
                    if not latest_visit.cholesterol_hdl:
                        missing_chol.append('cholesterol HDL')
                    missing_op.extend(missing_chol)
                
                sources_op['smoking'] = f"{smoking_status} ({smoking_info})"
                sources_op['diabetes'] = f"{'Tak' if has_diabetes else 'Nie'} (diagnoza)"
                
                if not missing_op:
                    possible_scores.append('SCORE2-OP')
                data_sources['SCORE2-OP'] = {'missing': missing_op, 'sources': sources_op}
            
            else:
                missing_data.append('Wiek poza zakresem 40-89 lat')
        
        context.update({
            'visit_data': visit_data,
            'smoking_status': smoking_status,
            'smoking_info': smoking_info,
            'has_diabetes': has_diabetes,
            'diabetes_age': diabetes_age,
            'latest_visit': latest_visit,
            'possible_scores': possible_scores,
            'missing_data': missing_data,
            'data_sources': data_sources,
            'score_stats': score_stats,
        })
        
        return context


class ImportDataView(View):
    template_name = 'patients/import_data.html'
    
    def get(self, request):
        return render(request, self.template_name)
    
    def post(self, request):
        if 'file' not in request.FILES:
            messages.error(request, 'Nie wybrano pliku do importu.')
            return render(request, self.template_name)
        
        uploaded_file = request.FILES['file']
        
        # Check file extension
        if not uploaded_file.name.lower().endswith(('.xlsx', '.xls')):
            messages.error(request, 'Obsługiwane są tylko pliki Excel (.xlsx, .xls).')
            return render(request, self.template_name)
        
        try:
            # Process the uploaded file
            results = self._process_excel_file(uploaded_file)
            
            messages.success(
                request, 
                f'Import zakończony! Przetworzono {results["patients_processed"]} pacjentów, '
                f'{results["visits_processed"]} wizyt, {results["diagnoses_processed"]} diagnoz.'
            )
            
            return redirect('patients:patient_list')
            
        except Exception as e:
            messages.error(request, f'Błąd podczas importu: {str(e)}')
            return render(request, self.template_name)
    
    def _process_excel_file(self, uploaded_file):
        """Process Excel file similar to the seeder script"""
        
        # Column mapping (same as in seeder)
        COLS = {
            "PACJENT": "full_name",
            "IDENTYFIAKTOR": "pesel",
            "DATA URODZENIA": "dob",
            "ROZPOZNANIE Z WIZYTY": "visit_dx",
            "DATA OSTATNIEJ WIZYTY": "visit_date",
            "ROZPOZNANIE PRZEWLEKŁE": "chronic_dx",
            "ADRES": "address",
            "TEL. KOMÓRKOWY": "phone_mobile",
            "TEL. STACJONARNY": "phone_landline",
            "DATA ROZPOZNANIA SCHORZENIA PRZEWLEKłEGO": "chronic_dx_date",
            "DATA OSTATNIEJ WIZYTY ZE SCHORZENIEM PRZEWLEKŁYM": "last_chronic_visit",
            "ŚR. CIśNIENIE SKURCZOWE": "systolic_pressure",
            "HEMOGLOBINA GLIKOWANA": "hba1c",
            "EGFR": "egfr",
            "CHOLESTEROL CAŁKOWITY": "cholesterol_total",
            "CHOLESTEROL HDL": "cholesterol_hdl",
        }
        
        # Read Excel file
        df_raw = pd.read_excel(uploaded_file)
        
        # Build rename map
        rename_map = self._build_rename_map(df_raw.columns.tolist(), COLS)
        
        # Rename columns
        df = df_raw.rename(columns=rename_map)
        
        # Check for missing columns
        missing = [v for v in COLS.values() if v not in df.columns]
        if missing:
            raise ValueError(f"Brakuje kolumn: {missing}")
        
        # Select only needed columns
        df = df.loc[:, list(COLS.values())]
        
        # Clean PESEL column
        df["pesel"] = df["pesel"].apply(lambda x: "" if pd.isna(x) else str(x))
        
        # Convert date columns
        for c in ("dob", "visit_date", "chronic_dx_date", "last_chronic_visit"):
            df[c] = df[c].apply(self._safe_date)
        
        # Handle NULL values for other columns
        for c in (
            "address", "phone_mobile", "phone_landline",
            "visit_dx", "chronic_dx",
            "systolic_pressure", "hba1c", "egfr",
            "cholesterol_total", "cholesterol_hdl"
        ):
            df[c] = df[c].apply(lambda x: None if pd.isna(x) else x)
        
        # Filter out rows with empty PESEL
        df = df[df["pesel"] != ""]
        
        # Process patients
        results = {
            'patients_processed': 0,
            'visits_processed': 0,
            'diagnoses_processed': 0
        }
        
        with transaction.atomic():
            for pesel, patient_group in df.groupby('pesel'):
                try:
                    patient_results = self._process_patient_group(patient_group)
                    results['patients_processed'] += 1
                    results['visits_processed'] += patient_results.get('visits', 0)
                    results['diagnoses_processed'] += patient_results.get('diagnoses', 0)
                except Exception as e:
                    print(f"Error processing patient {pesel}: {str(e)}")
                    continue
        
        return results
    
    def _canonical(self, s: str) -> str:
        """Normalize string for column matching"""
        s = unicodedata.normalize("NFKD", s)
        s = "".join(ch for ch in s if not unicodedata.combining(ch))
        s = s.upper()
        return re.sub(r"[^A-Z0-9]", "", s)
    
    def _build_rename_map(self, raw_cols, template):
        """Build column rename mapping"""
        canon_to_target = {self._canonical(k): v for k, v in template.items()}
        rename_map = {}
        for raw in raw_cols:
            c = self._canonical(raw)
            if c in canon_to_target:
                rename_map[raw] = canon_to_target[c]
        return rename_map
    
    def _pesel_to_gender(self, pv) -> str:
        """Extract gender from PESEL"""
        return "M" if int(str(pv)[-2]) % 2 else "F"
    
    def _safe_date(self, val):
        """Safely convert to date"""
        if pd.isna(val):
            return None
        try:
            return pd.to_datetime(val, dayfirst=True, errors="coerce").date()
        except:
            return None
    
    def _process_patient_group(self, patient_group):
        """Process all rows for a single patient"""
        first_row = patient_group.iloc[0]
        
        # Create or update patient
        patient, created = Patient.objects.get_or_create(
            pesel=first_row.pesel,
            defaults={
                'full_name': first_row.full_name,
                'date_of_birth': first_row.dob,
                'gender': self._pesel_to_gender(first_row.pesel),
                'address': first_row.address,
                'phone_mobile': first_row.phone_mobile,
                'phone_landline': first_row.phone_landline,
            }
        )
        
        if not created:
            # Update existing patient data (except basic info)
            if first_row.address:
                patient.address = first_row.address
            if first_row.phone_mobile:
                patient.phone_mobile = first_row.phone_mobile
            if first_row.phone_landline:
                patient.phone_landline = first_row.phone_landline
            patient.save()
        
        results = {'visits': 0, 'diagnoses': 0}
        
        # Create visit if visit_date exists and visit doesn't exist yet
        if first_row.visit_date:
            # build defaults dict, skipping any NaN/None
            raw = first_row  # your pandas Series
            defaults = {}
            for field in ('systolic_pressure','hba1c','egfr','cholesterol_total','cholesterol_hdl'):
                val = raw[field]
                # pd.isna covers both None and np.nan
                if not pd.isna(val):
                    defaults[field] = val

            visit, visit_created = Visit.objects.get_or_create(
                patient=patient,
                visit_date=first_row.visit_date,
                defaults=defaults
            )
            if visit_created:
                results['visits'] = 1
            
            # Add visit diagnoses
            if first_row.visit_dx:
                for code in str(first_row.visit_dx).split(','):
                    code = code.strip()
                    if code:
                        self._ensure_diagnosis(code)
                        VisitDiagnosis.objects.get_or_create(
                            visit=visit,
                            diagnosis_code=code
                        )
        
        # Process chronic diagnoses
        for _, row in patient_group.iterrows():
            if row.chronic_dx:
                self._ensure_diagnosis(row.chronic_dx)
                
                age_at = None
                if row.chronic_dx_date and row.dob:
                    age_at = relativedelta(row.chronic_dx_date, row.dob).years
                
                diagnosis, diag_created = PatientDiagnosis.objects.get_or_create(
                    patient=patient,
                    diagnosis_code=row.chronic_dx,
                    defaults={
                        'diagnosed_at': row.chronic_dx_date,
                        'last_visit_with_condition': row.last_chronic_visit,
                        'age_at_diagnosis': age_at,
                    }
                )
                
                if not diag_created:
                    # Update existing diagnosis
                    if row.chronic_dx_date:
                        diagnosis.diagnosed_at = row.chronic_dx_date
                    if row.last_chronic_visit:
                        diagnosis.last_visit_with_condition = row.last_chronic_visit
                    if age_at:
                        diagnosis.age_at_diagnosis = age_at
                    diagnosis.save()
                
                if diag_created:
                    results['diagnoses'] += 1
        
        return results
    
    def _ensure_diagnosis(self, code):
        """Ensure diagnosis code exists in database"""
        if code:
            Diagnosis.objects.get_or_create(code=code)


class PatientSearchView(View):
    """AJAX view for patient search autocomplete"""
    
    def get(self, request):
        query = request.GET.get('q', '')
        if len(query) < 2:
            return JsonResponse({'results': []})
        
        patients = Patient.objects.filter(
            Q(pesel__icontains=query) |
            Q(full_name__icontains=query)
        )[:10]
        
        results = []
        for patient in patients:
            results.append({
                'id': patient.id,
                'text': f"{patient.full_name or patient.pesel} ({patient.pesel})",
                'age': patient.age,
                'has_diabetes': patient.has_diabetes(),
            })
        
        return JsonResponse({'results': results})