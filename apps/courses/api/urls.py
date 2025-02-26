from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.courses.api.views import CourseViewSet, BatchViewSet

router = DefaultRouter()
router.register('courses', CourseViewSet)
router.register('batches', BatchViewSet)

urlpatterns = [
    path('', include(router.urls)),
]
