from django.db import models
from django.core.validators import RegexValidator
from datetime import date
from dateutil.relativedelta import relativedelta
from django.db.models import Q 
from django.core.exceptions import ValidationError

class Patient(models.Model):
    GENDER_CHOICES = [
        ('M', 'Mężczyzna'),
        ('F', 'Kobieta'),
    ]

    SMOKING_CHOICES = [
        ('non_smoker', 'Nie pali'),
        ('smoker', 'Pali'),
        ('assumed_non_smoker', 'Zakładany niepali (brak danych)'),
    ]
    
    pesel = models.CharField(
        max_length=11, 
        unique=True, 
        validators=[RegexValidator(regex=r'^\d{11}$', message='PESEL musi zawierać 11 cyfr')]
    )
    full_name = models.CharField(max_length=200, blank=True, null=True)
    date_of_birth = models.DateField()
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES)
    address = models.TextField(blank=True, null=True)
    phone_mobile = models.CharField(max_length=20, blank=True, null=True)
    phone_landline = models.CharField(max_length=20, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    smoking_status = models.CharField(
        max_length=20, 
        choices=SMOKING_CHOICES, 
        default='assumed_non_smoker'
    )
    
    class Meta:
        db_table = 'patients'
        ordering = ['full_name', 'pesel']
    
    def __str__(self):
        return f"{self.full_name or self.pesel} ({self.pesel})"
    
    def calculate_age(self, reference_date=None):
        """Calculate age at reference date (default: today)"""
        if reference_date is None:
            reference_date = date.today()
        return reference_date.year - self.date_of_birth.year - (
            (reference_date.month, reference_date.day) < (self.date_of_birth.month, self.date_of_birth.day)
        )

    @property
    def age(self):
        return self.calculate_age()
    
    def get_latest_visit(self):
        """Get the most recent visit for this patient"""
        return self.visits.order_by('-visit_date').first()
    
    def has_diabetes(self) -> bool:
        codes = ['E10', 'E11', 'E13', 'E14']
        q = Q()
        for c in codes:
            q |= Q(chronic_diagnoses__diagnosis_code__startswith=c)
            q |= Q(visits__diagnoses__diagnosis_code__startswith=c)
        return self.__class__.objects.filter(pk=self.pk).filter(q).exists()
    
    def get_diabetes_age_at_diagnosis(self):
        """Get age at first diabetes diagnosis"""
        diabetes_codes = ['E10', 'E11', 'E13', 'E14']

        # Build a Q that matches any diagnosis_code starting with one of the codes
        prefix_q = Q()
        for c in diabetes_codes:
            prefix_q |= Q(diagnosis_code__startswith=c)

        # Now filter chronic_diagnoses using that Q plus age_at_diagnosis not null
        chronic_dx = (
            self.chronic_diagnoses
                .filter(prefix_q, age_at_diagnosis__isnull=False)
                .order_by('diagnosed_at')
                .first()
        )

        if chronic_dx:
            return float(chronic_dx.age_at_diagnosis)
        return None
    
    def get_smoking_status(self):
        """Determine smoking status based on diagnoses with priority over user setting"""
        # Check diagnoses first
        all_diagnoses = set()
        chronic = self.chronic_diagnoses.values_list('diagnosis_code', flat=True)
        all_diagnoses.update(chronic)
        visit_dx = VisitDiagnosis.objects.filter(
            visit__patient=self
        ).values_list('diagnosis_code', flat=True)
        all_diagnoses.update(visit_dx)
        
        # Priority 1: Smoker diagnosis (F17.2 - zaburzenia z powodu nikotyny)
        if 'F17.2' in all_diagnoses:
            return 'smoker', 'F17.2'
        
        # Priority 2: Non-smoker diagnoses
        if 'Z87.7' in all_diagnoses:
            return 'non_smoker', 'Z87.7'  # Wywiad osobniczy dotyczący palenia tytoniu
        elif 'Z58.7' in all_diagnoses:
            return 'non_smoker', 'Z58.7'  # Narażenie na dym tytoniowy
        
        # Priority 3: Ex-smoker diagnoses (if we want to add them in the future)
        # elif 'Z87.891' in all_diagnoses:  # Personal history of nicotine dependence
        #     return 'ex_smoker', 'diagnosis_Z87.891'
        
        # Priority 4: User setting from model field
        return self.smoking_status, 'patient_setting'   



class Diagnosis(models.Model):
    code = models.CharField(max_length=10, unique=True, primary_key=True)
    description = models.TextField(blank=True, null=True)
    
    class Meta:
        db_table = 'diagnoses'
        ordering = ['code']
    
    def __str__(self):
        return f"{self.code}: {self.description or 'Brak opisu'}"


class PatientDiagnosis(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='chronic_diagnoses')
    diagnosis_code = models.CharField(max_length=10)
    diagnosed_at = models.DateField(blank=True, null=True)
    last_visit_with_condition = models.DateField(blank=True, null=True)
    age_at_diagnosis = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'patient_diagnoses'
        unique_together = ['patient', 'diagnosis_code']
        ordering = ['diagnosed_at']
    
    def __str__(self):
        return f"{self.patient.pesel} - {self.diagnosis_code}"


class Visit(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='visits')
    visit_date = models.DateField()
    quarter = models.CharField(max_length=10, blank=True, null=True)
    systolic_pressure = models.IntegerField(blank=True, null=True)
    hba1c = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    egfr = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    cholesterol_total = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    cholesterol_hdl = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'visits'
        ordering = ['-visit_date']
    
    def __str__(self):
        return f"{self.patient.pesel} - {self.visit_date}"
    
    def get_quarter(self):
        """Generate quarter string from visit date"""
        if self.visit_date:
            q = ((self.visit_date.month - 1) // 3) + 1
            return f"{self.visit_date.year}H{q}"
        return None
    
    def clean(self):
        if self.patient and self.visit_date:
            existing = Visit.objects.filter(
                patient=self.patient,
                visit_date=self.visit_date
            ).exclude(pk=self.pk)
            if existing.exists():
                raise ValidationError('Wizyta w tym dniu już istnieje dla tego pacjenta.')
    
    def save(self, *args, **kwargs):
        self.clean()
        if self.visit_date and not self.quarter:
            self.quarter = self.get_quarter()
        super().save(*args, **kwargs)


class VisitDiagnosis(models.Model):
    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name='diagnoses')
    diagnosis_code = models.CharField(max_length=10)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'visit_diagnoses'
        unique_together = ['visit', 'diagnosis_code']
    
    def __str__(self):
        return f"{self.visit.patient.pesel} ({self.visit.visit_date}) - {self.diagnosis_code}"