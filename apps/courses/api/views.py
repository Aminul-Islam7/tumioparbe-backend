from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.courses.models import Course, Batch
from apps.courses.api.serializers import CourseSerializer, BatchSerializer
from apps.enrollments.models import Enrollment


class IsStaffOrReadOnly(permissions.BasePermission):
    """
    Permission class that allows read access to everyone,
    but only allows write access to staff users.
    """

    def has_permission(self, request, view):
        # Debug message to help troubleshoot
        print(f"IsStaffOrReadOnly check: Method={request.method}, is_staff={request.user.is_staff}")

        # Allow GET, HEAD, OPTIONS requests for everyone
        if request.method in permissions.SAFE_METHODS:
            return True
        # Otherwise, only allow if user is staff
        return request.user and request.user.is_staff


class IsStaffUser(permissions.BasePermission):
    """
    Permission class that only allows access to staff users.
    """

    def has_permission(self, request, view):
        return request.user and request.user.is_staff


class CourseViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Course model.
    - Regular users can only view active courses
    - Staff users can create, update, and delete courses
    """
    queryset = Course.objects.all()
    serializer_class = CourseSerializer
    permission_classes = [IsStaffOrReadOnly]

    def get_queryset(self):
        """
        Regular users should only see active courses.
        Staff users can see all courses.
        """
        if self.request.user.is_staff:
            return Course.objects.all()
        return Course.objects.filter(is_active=True)

    @action(detail=False, methods=['get'])
    def check_permissions(self, request):
        """
        Debug endpoint to check if user has proper permissions
        """
        user = request.user
        return Response({
            'username': user.username,
            'is_staff': user.is_staff,
            'is_admin': getattr(user, 'is_admin', False),
            'is_superuser': user.is_superuser,
            'can_create_course': user.is_staff,
            'permissions': [perm.codename for perm in user.user_permissions.all()]
        })

    def destroy(self, request, *args, **kwargs):
        """
        Override destroy method to prevent deletion if there are batches or enrollments
        Instead of deleting, mark the course as inactive
        """
        course = self.get_object()

        # Check if there are any batches for this course
        has_batches = course.batches.exists()
        
        # Check if there are any active enrollments for this course
        has_enrollments = Enrollment.objects.filter(
            batch__course=course,
            is_active=True
        ).exists()

        if has_batches or has_enrollments:
            # Don't delete but mark as inactive
            course.is_active = False
            course.save()
            
            message = "Course marked as inactive instead of being deleted."
            if has_batches:
                message = "Course has batches. " + message
            if has_enrollments:
                message = "Course has active enrollments. " + message
                
            return Response(
                {"message": message},
                status=status.HTTP_200_OK
            )
        else:
            # If no batches or enrollments, proceed with deletion
            return super().destroy(request, *args, **kwargs)


class BatchViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Batch model.
    - Regular users can only view visible batches of active courses they're enrolled in
    - Staff users can create, update, and delete batches
    """
    queryset = Batch.objects.all()
    serializer_class = BatchSerializer
    permission_classes = [IsStaffOrReadOnly]

    def get_queryset(self):
        """
        Filter batches based on user role:
        - Staff see all batches
        - Regular users see visible batches plus any they're enrolled in
        """
        if self.request.user.is_staff:
            return Batch.objects.all()

        # For regular users:
        user = self.request.user

        # Get all batches where is_visible=True
        visible_batches = Batch.objects.filter(is_visible=True, course__is_active=True)

        # Also include batches the user's students are enrolled in, even if not visible
        enrolled_batch_ids = Enrollment.objects.filter(
            student__parent=user,
            is_active=True
        ).values_list('batch_id', flat=True)

        enrolled_batches = Batch.objects.filter(id__in(enrolled_batch_ids))

        # Combine querysets
        return (visible_batches | enrolled_batches).distinct()

    def destroy(self, request, *args, **kwargs):
        """
        Override destroy method to prevent deletion if there are enrolled students
        Instead of deleting, mark the batch as invisible if it has enrollments
        """
        batch = self.get_object()

        # Check if there are any active enrollments for this batch
        has_enrollments = Enrollment.objects.filter(
            batch=batch,
            is_active=True
        ).exists()

        if has_enrollments:
            # If there are enrollments, don't delete but mark as invisible
            batch.is_visible = False
            batch.save()
            return Response(
                {"message": "Batch has active enrollments. Marked as invisible instead of deleted."},
                status=status.HTTP_200_OK
            )
        else:
            # If no enrollments, proceed with deletion
            return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['get'])
    def enrolled_students(self, request, pk=None):
        """
        Returns the list of students enrolled in this batch.
        Only available to staff users or parents with children enrolled in the batch.
        """
        batch = self.get_object()
        user = request.user

        # Staff can see all students
        if user.is_staff:
            enrollments = Enrollment.objects.filter(batch=batch, is_active=True)
            from apps.accounts.api.serializers import StudentSerializer
            student_data = [
                StudentSerializer(enrollment.student).data
                for enrollment in enrollments
            ]
            return Response(student_data)

        # Regular users can only see if they have a student enrolled
        has_enrolled_student = Enrollment.objects.filter(
            batch=batch,
            student__parent=user,
            is_active=True
        ).exists()

        if has_enrolled_student:
            enrollments = Enrollment.objects.filter(batch=batch, is_active=True)
            student_data = [
                {"id": enrollment.student.id, "name": enrollment.student.name}
                for enrollment in enrollments
            ]
            return Response(student_data)

        return Response(
            {"error": "You do not have permission to view this information"},
            status=status.HTTP_403_FORBIDDEN
        )

    @action(detail=True, methods=['post'], permission_classes=[IsStaffUser])
    def transfer_students(self, request, pk=None):
        """
        Admin-only endpoint to transfer students from one batch to another.
        Requires student IDs and destination batch ID.
        """
        source_batch = self.get_object()
        destination_batch_id = request.data.get('destination_batch_id')
        student_ids = request.data.get('student_ids', [])

        if not destination_batch_id:
            return Response(
                {"error": "Destination batch ID is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not student_ids:
            return Response(
                {"error": "At least one student ID is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            destination_batch = Batch.objects.get(id=destination_batch_id)
        except Batch.DoesNotExist:
            return Response(
                {"error": "Destination batch not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Check if destination batch is in the same course
        if source_batch.course_id != destination_batch.course_id:
            return Response(
                {"error": "Cannot transfer students to a batch from a different course"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get all enrollments for the given student_ids in the source batch
        enrollments = Enrollment.objects.filter(
            student_id__in=student_ids,
            batch=source_batch,
            is_active=True
        )

        if not enrollments.exists():
            return Response(
                {"error": "No active enrollments found for the given students in this batch"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Transfer each enrollment to the destination batch
        transferred_students = []
        for enrollment in enrollments:
            # Check if student is already enrolled in the destination batch
            existing_enrollment = Enrollment.objects.filter(
                student=enrollment.student,
                batch=destination_batch,
                is_active=True
            ).first()

            if existing_enrollment:
                # Skip if already enrolled
                continue

            # Update the batch for this enrollment
            enrollment.batch = destination_batch
            enrollment.save()
            transferred_students.append({
                'id': enrollment.student.id,
                'name': enrollment.student.name
            })

        return Response({
            'source_batch': {
                'id': source_batch.id,
                'name': source_batch.name
            },
            'destination_batch': {
                'id': destination_batch.id,
                'name': destination_batch.name
            },
            'transferred_students': transferred_students,
            'count': len(transferred_students)
        }, status=status.HTTP_200_OK)
