from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.enrollments.api.views import EnrollmentViewSet, CouponViewSet

router = DefaultRouter()
router.register('enrollments', EnrollmentViewSet)
router.register('coupons', CouponViewSet)

urlpatterns = [
    path('', include(router.urls)),
]
