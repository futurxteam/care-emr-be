from logging import Logger

from celery import shared_task
from celery.utils.log import get_task_logger
from django.utils import timezone

from care.emr.models import TokenSlot

logger: Logger = get_task_logger(__name__)


@shared_task
def cleanup_expired_token_slots():
    """
    Hard-deletes TokenSlot objects that have expired if they have no bookings associated with them.
    Also cancels pending payment bookings that have exceeded the 10-minute payment window.
    """
    logger.info("Cleaning up expired TokenSlot objects")
    queryset = TokenSlot.objects.filter(
        tokenbooking__isnull=True, end_datetime__lte=timezone.now()
    )
    queryset.delete()

    logger.info("Cleaning up expired pending payment bookings")
    from datetime import timedelta
    from care.emr.models import TokenBooking
    from care.emr.resources.charge_item.spec import ChargeItemStatusOptions

    expired_pending_bookings = TokenBooking.objects.filter(
        status="payment_pending",
        created_date__lte=timezone.now() - timedelta(minutes=10)
    )
    for booking in expired_pending_bookings:
        # Decrement slot allocation
        slot = booking.token_slot
        slot.allocated = max(0, slot.allocated - 1)
        slot.save(update_fields=["allocated"])

        # Cancel the associated charge item
        if booking.charge_item:
            booking.charge_item.status = ChargeItemStatusOptions.aborted.value
            booking.charge_item.save(update_fields=["status"])

        # Cancel the booking
        booking.status = "cancelled"
        booking.note = "Payment window expired"
        booking.save(update_fields=["status", "note"])

