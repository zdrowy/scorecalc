from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from patients.models import Patient, Visit
from datetime import date
import math
from decimal import Decimal
from typing import Union, Literal


class Score2Result(models.Model):
    SCORE_TYPE_CHOICES = [
        ('SCORE2', 'SCORE2'),
        ('SCORE2-Diabetes', 'SCORE2-Diabetes'),
        ('SCORE2-OP', 'SCORE2-OP'),
    ]
    DATA_SOURCE_CHOICES = [
        ('visit', 'Dane z wizyty'),
        ('previous_visit', 'Dane z poprzednich wizyt'),
        ('median', 'Mediana dla grupy wiekowej'),
        ('mixed', 'Dane mieszane'),
    ]
    
    RISK_LEVEL_CHOICES = [
        ('low_to_moderate', 'Niskie do umiarkowanego'),
        ('high', 'Wysokie'),
        ('very_high', 'Bardzo wysokie'),
        ('not_applicable', 'Nie dotyczy'),
        ('age_out_of_range', 'Wiek poza zakresem'),
    ]
    
    REGION_CHOICES = [
        ('low', 'Niskie ryzyko'),
        ('moderate', 'Umiarkowane ryzyko'),
        ('high', 'Wysokie ryzyko'),
        ('very_high', 'Bardzo wysokie ryzyko'),
    ]
    
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='score2_results')
    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name='score2_results')
    score_type = models.CharField(max_length=20, choices=SCORE_TYPE_CHOICES)
    score_value = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        blank=True, 
        null=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    risk_level = models.CharField(max_length=20, choices=RISK_LEVEL_CHOICES)
    region = models.CharField(max_length=10, choices=REGION_CHOICES, default='high')
    
    # Input values used for calculation
    age_at_calculation = models.IntegerField()
    systolic_pressure = models.IntegerField(blank=True, null=True)
    cholesterol_total = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    cholesterol_hdl = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    smoking_status = models.CharField(max_length=50, blank=True, null=True)
    smoking_info_source = models.CharField(max_length=100, blank=True, null=True)
    
    # Diabetes-specific fields
    has_diabetes = models.BooleanField(default=False)
    age_at_diabetes_diagnosis = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    hba1c = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    egfr = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    
    # Metadata
    cholesterol_info = models.TextField(blank=True, null=True)
    calculation_notes = models.TextField(blank=True, null=True)
    is_calculation_successful = models.BooleanField(default=False)
    missing_data_reason = models.TextField(blank=True, null=True)
    data_source = models.CharField(
        max_length=20, 
        choices=DATA_SOURCE_CHOICES,
        default='visit'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'score2_results'
        ordering = ['-score_value', '-created_at'] 
        unique_together = ['patient', 'visit']  
    
    def __str__(self):
        score_display = f"{self.score_value}%" if self.score_value else "Brak wyniku"
        return f"{self.patient.pesel} - {self.score_type} - {score_display} ({self.visit.visit_date})"
    
    @property
    def score_display(self):
        """Display score value or reason for failure"""
        if self.is_calculation_successful and self.score_value is not None:
            return f"{self.score_value}%"
        elif self.missing_data_reason:
            return f"Brak danych: {self.missing_data_reason}"
        else:
            return "Błąd obliczenia"
    
    @property
    def risk_level_display(self):
        """Get Polish display name for risk level"""
        risk_dict = dict(self.RISK_LEVEL_CHOICES)
        return risk_dict.get(self.risk_level, self.risk_level)
    
    @staticmethod
    def _convert_to_float(value: Union[float, int, Decimal, None]) -> float:
        """Convert various numeric types to float, handling Decimal objects"""
        if value is None:
            raise ValueError("Cannot convert None to float")
        if isinstance(value, Decimal):
            return float(value)
        return float(value)
    
    @staticmethod
    def calc_hb(hb):
        """Convert HbA1c percentage to mmol/mol"""
        return (hb - 2.15) * 10.929
    
    @staticmethod
    def calculate_score2(age: float, sbp: float, tchol: float, hdl: float, 
                        smoker: bool, sex: str = "M", region: str = "high") -> float:
        """SCORE2 calculation function for non-diabetic patients aged 40-69"""
        
        # Convert inputs to float to handle Decimal types
        age = Score2Result._convert_to_float(age)
        sbp = Score2Result._convert_to_float(sbp)
        tchol = Score2Result._convert_to_float(tchol)
        hdl = Score2Result._convert_to_float(hdl)
        
        # SCORE2 constants
        BETA = {
            "M": {
                "cage": 0.3742, "smoke": 0.6012, "csbp": 0.2777,
                "ctchol": 0.1458, "chdl": -0.2698,
                "smoke_cage": -0.0755, "csbp_cage": -0.0255,
                "ctchol_cage": -0.0281, "chdl_cage": 0.0426,
                "baseline_survival": 0.9605,
            },
            "F": {
                "cage": 0.4648, "smoke": 0.7744, "csbp": 0.3131,
                "ctchol": 0.1002, "chdl": -0.2606,
                "smoke_cage": -0.1088, "csbp_cage": -0.0277,
                "ctchol_cage": -0.0226, "chdl_cage": 0.0613,
                "baseline_survival": 0.9776,
            },
        }

        SCALES = {
            "M": {
                "low": (-0.5699, 0.7476), "moderate": (-0.1565, 0.8009),
                "high": (0.3207, 0.9360), "very_high": (0.5836, 0.8294),
            },
            "F": {
                "low": (-0.7380, 0.7019), "moderate": (-0.3143, 0.7701),
                "high": (0.5710, 0.9369), "very_high": (0.9412, 0.8329),
            },
        }
        
        MGDL_TO_MMOL = 1 / 38.67
        
        def _to_mmol(value: float) -> float:
            """Convert mg/dL to mmol/L if needed"""
            return value * MGDL_TO_MMOL if value > 20 else value

        tchol = _to_mmol(tchol)
        hdl = _to_mmol(hdl)

        β = BETA[sex]
        cage = (age - 60) / 5
        csbp = (sbp - 120) / 20
        ctchol = tchol - 6
        chdl = (hdl - 1.3) / 0.5
        smoke = 1 if smoker else 0

        x = (
            β["cage"]*cage + β["smoke"]*smoke + β["csbp"]*csbp +
            β["ctchol"]*ctchol + β["chdl"]*chdl +
            β["smoke_cage"]*smoke*cage + β["csbp_cage"]*csbp*cage +
            β["ctchol_cage"]*ctchol*cage + β["chdl_cage"]*chdl*cage
        )

        S0 = β["baseline_survival"]
        r_uncal = 1 - S0 ** math.exp(x)
        r_uncal = max(1e-15, min(1 - 1e-15, r_uncal))

        s1, s2 = SCALES[sex][region]
        y = s1 + s2 * math.log(-math.log(1 - r_uncal))
        r_cal = 1 - math.exp(-math.exp(y))

        return round(r_cal * 100, 2)
    
    @staticmethod
    def calculate_score2_diabetes(age: float, sbp: float, tchol: float, hdl: float, 
                                smoker: bool, diabetes: bool, age_at_diagnosis: float = None,
                                a1c: float = None, egfr: float = None, sex: str = "M", 
                                region: str = "high") -> float:
        """SCORE2-Diabetes calculation function for diabetic patients aged 40+"""
        
        # Convert inputs to float to handle Decimal types
        age = Score2Result._convert_to_float(age)
        sbp = Score2Result._convert_to_float(sbp)
        tchol = Score2Result._convert_to_float(tchol)
        hdl = Score2Result._convert_to_float(hdl)
        if age_at_diagnosis is not None:
            age_at_diagnosis = Score2Result._convert_to_float(age_at_diagnosis)
        if a1c is not None:
            a1c = Score2Result._convert_to_float(a1c)
            a1c = Score2Result.calc_hb(a1c)
        if egfr is not None:
            egfr = Score2Result._convert_to_float(egfr)
        
        # SCORE2-Diabetes constants
        BETA = {
            "M": {
                "cage": 0.5368, "smoke": 0.4774, "csbp": 0.1322, "diab": 0.6457,
                "ctchol": 0.1102, "chdl": -0.1087, "smoke_cage": -0.0672,
                "csbp_cage": -0.0268, "diab_cage": -0.0983, "ctchol_cage": -0.0181,
                "chdl_cage": 0.0095, "cagediab": -0.0998, "ca1c": 0.0955,
                "cegfr": -0.0591, "cegfr2": 0.0058, "ca1c_cage": -0.0134,
                "cegfr_cage": 0.0115, "baseline_survival": 0.9605,
            },
            "F": {
                "cage": 0.6624, "smoke": 0.6139, "csbp": 0.1421, "diab": 0.8096,
                "ctchol": 0.1127, "chdl": -0.1568, "smoke_cage": -0.1122,
                "csbp_cage": -0.0167, "diab_cage": -0.1272, "ctchol_cage": -0.0200,
                "chdl_cage": 0.0186, "cagediab": -0.1180, "ca1c": 0.1173,
                "cegfr": -0.0640, "cegfr2": 0.0062, "ca1c_cage": -0.0196,
                "cegfr_cage": 0.0169, "baseline_survival": 0.9776,
            },
        }

        SCALES = {
            "M": {
                "low": (-0.5699, 0.7476), "moderate": (-0.1565, 0.8009),
                "high": (0.3207, 0.9360), "very_high": (0.5836, 0.8294),
            },
            "F": {
                "low": (-0.7380, 0.7019), "moderate": (-0.3143, 0.7701),
                "high": (0.5710, 0.9369), "very_high": (0.9412, 0.8329),
            },
        }
        
        MGDL_TO_MMOL = 1 / 38.67
        
        def _chol_to_mmol(value: float) -> float:
            return value * MGDL_TO_MMOL if value > 20 else value

        if not 40 <= age <= 69:
            raise ValueError("SCORE2‑Diabetes validated for age 40‑69 years")

        if diabetes and age_at_diagnosis is None:
            raise ValueError("age_at_diagnosis required for diabetes patients")
        
        if a1c is None:
            a1c = 31.0  # ~5.0% (normal)
        if egfr is None:
            egfr = 95.0  # typical normal value

        tchol = _chol_to_mmol(tchol)
        hdl = _chol_to_mmol(hdl)

        β = BETA[sex]

        cage = (age - 60) / 5
        csbp = (sbp - 120) / 20
        ctchol = tchol - 6
        chdl = (hdl - 1.3) / 0.5
        smoke = 1 if smoker else 0
        diab = 1 if diabetes else 0
        cagediab = diab * ((age_at_diagnosis - 50) / 5 if age_at_diagnosis else 0)
        ca1c = (a1c - 31) / 9.34
        ln_egfr = math.log(egfr)
        cegfr = (ln_egfr - 4.5) / 0.15
        cegfr2 = cegfr ** 2

        x = (
            β["cage"] * cage + β["smoke"] * smoke + β["csbp"] * csbp +
            β["diab"] * diab + β["ctchol"] * ctchol + β["chdl"] * chdl +
            β["smoke_cage"] * smoke * cage + β["csbp_cage"] * csbp * cage +
            β["diab_cage"] * diab * cage + β["ctchol_cage"] * ctchol * cage +
            β["chdl_cage"] * chdl * cage + β["cagediab"] * cagediab +
            β["ca1c"] * ca1c + β["cegfr"] * cegfr + β["cegfr2"] * cegfr2 +
            β["ca1c_cage"] * ca1c * cage + β["cegfr_cage"] * cegfr * cage
        )

        S0 = β["baseline_survival"]
        r_uncal = 1 - S0 ** math.exp(x)

        eps = 1e-15
        r_uncal = max(eps, min(1 - eps, r_uncal))

        s1, s2 = SCALES[sex][region]
        y = s1 + s2 * math.log(-math.log(1 - r_uncal))
        r_cal = 1 - math.exp(-math.exp(y))

        return round(r_cal * 100, 2)
    
    @staticmethod
    def calculate_score2_op(age: float, sbp: float, tchol: float, hdl: float, 
                          smoker: bool, diabetes: bool, sex: str = "M", region: str = "moderate") -> float:
        """SCORE2-OP calculation function for patients aged 70-89"""
        
        # Convert inputs to float to handle Decimal types
        age = Score2Result._convert_to_float(age)
        sbp = Score2Result._convert_to_float(sbp)
        tchol = Score2Result._convert_to_float(tchol)
        hdl = Score2Result._convert_to_float(hdl)
        
        # SCORE2-OP constants
        BETA = {
            "M": {
                "cage": 0.0634, "diab": 0.4245, "smoke": 0.3524, "csbp": 0.0094,
                "ctchol": 0.0850, "chdl": -0.3564, "diab_cage": -0.0174,
                "smoke_cage": -0.0247, "csbp_cage": -0.0005, "ctchol_cage": 0.0073,
                "chdl_cage": 0.0091, "baseline_survival": 0.7576, "mean_lp": 0.0929,
            },
            "F": {
                "cage": 0.0789, "diab": 0.6010, "smoke": 0.4921, "csbp": 0.0102,
                "ctchol": 0.0605, "chdl": -0.3040, "diab_cage": -0.0107,
                "smoke_cage": -0.0255, "csbp_cage": -0.0004, "ctchol_cage": -0.0009,
                "chdl_cage": 0.0154, "baseline_survival": 0.8082, "mean_lp": 0.2290,
            },
        }

        SCALES = {
            "M": {
                "low": (-0.34, 1.19), "moderate": (0.01, 1.25),
                "high": (0.08, 1.15), "very_high": (0.05, 0.70),
            },
            "F": {
                "low": (-0.52, 1.01), "moderate": (-0.10, 1.10),
                "high": (0.38, 1.09), "very_high": (0.38, 0.69),
            },
        }

        MGDL_TO_MMOL = 1 / 38.67

        def _chol_to_mmol(value: float) -> float:
            return value * MGDL_TO_MMOL if value > 20 else value

        if not 70 <= age <= 89:
            raise ValueError("SCORE2‐OP validated for age 70‐89 years")

        tchol = _chol_to_mmol(tchol)
        hdl = _chol_to_mmol(hdl)

        β = BETA[sex]

        cage = age - 73
        csbp = sbp - 150
        ctchol = tchol - 6
        chdl = hdl - 1.4
        diab = 1 if diabetes else 0
        smoke = 1 if smoker else 0

        x = (
            β["cage"] * cage + β["diab"] * diab + β["smoke"] * smoke +
            β["csbp"] * csbp + β["ctchol"] * ctchol + β["chdl"] * chdl +
            β["diab_cage"] * diab * cage + β["smoke_cage"] * smoke * cage +
            β["csbp_cage"] * csbp * cage + β["ctchol_cage"] * ctchol * cage +
            β["chdl_cage"] * chdl * cage
        )

        S0 = β["baseline_survival"]
        mlp = β["mean_lp"]
        r_uncal = 1 - S0 ** math.exp(x - mlp)

        eps = 1e-15
        r_uncal = max(eps, min(1 - eps, r_uncal))

        s1, s2 = SCALES[sex][region]
        y = s1 + s2 * math.log(-math.log(1 - r_uncal))
        r_cal = 1 - math.exp(-math.exp(y))

        return round(r_cal * 100, 2)
    
    @staticmethod
    def get_risk_level(age: int, score_value, score_type: str = "SCORE2") -> str:
        """Determine risk level based on age and score value"""
        # If score is not a number, return not applicable
        try:
            score = float(score_value)
        except (ValueError, TypeError):
            return 'not_applicable'
        
        # SCORE2-OP uses different thresholds for 70+ age group
        if score_type == "SCORE2-OP" or age >= 70:
            if score < 7.5:
                return 'low_to_moderate'
            elif score < 15.0:
                return 'high'
            else:
                return 'very_high'
        else:
            # SCORE2 and SCORE2-Diabetes thresholds
            if age < 50:
                if score < 2.5:
                    return 'low_to_moderate'
                elif score < 7.5:
                    return 'high'
                else:
                    return 'very_high'
            elif 50 <= age <= 69:
                if score < 5.0:
                    return 'low_to_moderate'
                elif score < 10.0:
                    return 'high'
                else:
                    return 'very_high'
            else:
                return 'age_out_of_range'