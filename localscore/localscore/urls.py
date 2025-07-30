from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # Redirect root to patients list
    path('', RedirectView.as_view(pattern_name='patients:patient_list', permanent=False)),
    
    # App URLs
    path('patients/', include('patients.urls')),
    path('score2/', include('score2.urls')),
]