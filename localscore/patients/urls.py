from django.urls import path
from . import views

app_name = 'patients'

urlpatterns = [
    # Patient list and search
    path('', views.PatientListView.as_view(), name='patient_list'),
    path('search/', views.PatientSearchView.as_view(), name='patient_search'),
    
    # Patient detail
    path('<int:pk>/', views.PatientDetailView.as_view(), name='patient_detail'),
    
    # Data import
    path('import/', views.ImportDataView.as_view(), name='import_data'),
]