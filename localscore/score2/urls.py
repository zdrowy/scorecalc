from django.urls import path
from . import views

app_name = 'score2'

urlpatterns = [
    # Statistics
    path('stats/', views.Score2StatsView.as_view(), name='stats'),
    
    # Calculate SCORE2
    path('calculate/<int:patient_id>/', views.CalculateScore2View.as_view(), name='calculate_single'),
    path('calculate-all/', views.CalculateAllScore2View.as_view(), name='calculate_all'),
]