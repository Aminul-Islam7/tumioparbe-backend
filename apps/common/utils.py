from apps.common.models import ActivityLog


def log_activity(user, action_type, **metadata):
    """
    Log an activity with the specified user, action type and metadata.

    Args:
        user: The user who performed the action (User instance)
        action_type: The type of action performed (must be one of ActivityLog.ACTION_TYPES)
        **metadata: Additional context data to store with the activity log

    Returns:
        The created ActivityLog instance
    """
    # Validate action_type
    valid_action_types = dict(ActivityLog.ACTION_TYPES).keys()
    if action_type not in valid_action_types:
        valid_types = ', '.join(valid_action_types)
        raise ValueError(f"Invalid action_type '{action_type}'. Must be one of: {valid_types}")

    # Create and return the activity log
    return ActivityLog.objects.create(
        user=user,
        action_type=action_type,
        metadata=metadata
    )
