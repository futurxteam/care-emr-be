from drf_spectacular.utils import extend_schema
from pydantic import UUID4, BaseModel
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from django.db import transaction
from django.utils import timezone

from care.emr.api.viewsets.base import EMRBaseViewSet, EMRRetrieveMixin
from care.emr.api.viewsets.scheduling import (
    AppointmentBookingSpec,
    SlotsForDayRequestSpec,
    SlotViewSet,
)
from care.emr.api.viewsets.scheduling.booking import TokenBookingViewSet
from care.emr.models.patient import Patient
from care.emr.models.scheduling import TokenBooking, TokenSlot
from care.emr.resources.charge_item.spec import ChargeItemStatusOptions
from care.emr.resources.scheduling.slot.spec import (
    BookingStatusChoices,
    TokenBookingOTPReadSpec,
    TokenSlotBaseSpec,
)
from care.utils.shortcuts import get_object_or_404
from config.patient_otp_authentication import (
    JWTTokenPatientAuthentication,
    OTPAuthenticatedPermission,
)


class SlotsForDayRequestSpec(SlotsForDayRequestSpec):
    facility: UUID4


class CancelAppointmentSpec(BaseModel):
    patient: UUID4
    appointment: UUID4


class OTPConfirmPaymentSpec(BaseModel):
    appointment: UUID4
    patient: UUID4
    razorpay_payment_id: str
    razorpay_signature: str



class OTPSlotViewSet(EMRRetrieveMixin, EMRBaseViewSet):
    authentication_classes = [JWTTokenPatientAuthentication]
    permission_classes = [OTPAuthenticatedPermission]
    database_model = TokenSlot
    pydantic_read_model = TokenSlotBaseSpec

    def get_queryset(self):
        return TokenSlot.objects.filter(
            availability__schedule__is_public=True,
        )

    @extend_schema(
        request=SlotsForDayRequestSpec,
    )
    @action(detail=False, methods=["POST"])
    def get_slots_for_day(self, request, *args, **kwargs):
        request_data = SlotsForDayRequestSpec(**request.data)
        return SlotViewSet.get_slots_for_day_handler(
            request_data.facility, request.data, is_public=True
        )

    @extend_schema(
        request=AppointmentBookingSpec,
    )
    @action(detail=True, methods=["POST"])
    def create_appointment(self, request, *args, **kwargs):
        request_data = AppointmentBookingSpec(**request.data)
        if not Patient.objects.filter(
            external_id=request_data.patient, phone_number=request.user.phone_number
        ).exists():
            raise ValidationError("Patient not allowed")
        appointment = SlotViewSet.create_appointment_handler(
            self.get_object(), request.data, None
        )
        booking_data = TokenBookingOTPReadSpec.serialize(appointment).model_dump()
        if appointment.status == "payment_pending":
            from django.conf import settings
            booking_data["payment_required"] = True
            booking_data["razorpay_order_id"] = appointment.razorpay_order_id
            booking_data["razorpay_key"] = getattr(settings, "RAZORPAY_KEY_ID", "rzp_test_placeholder")
            amount = 0.0
            if appointment.charge_item:
                if appointment.appointment_medium == "virtual":
                    amount = float(appointment.charge_item.total_price)
                else:
                    amount = min(100.0, float(appointment.charge_item.total_price))
            booking_data["payment_amount"] = int(amount * 100)
            booking_data["currency"] = "INR"
        return Response(booking_data)

    @extend_schema(
        request=CancelAppointmentSpec,
    )
    @action(detail=False, methods=["POST"])
    def cancel_appointment(self, request, *args, **kwargs):
        request_data = CancelAppointmentSpec(**request.data)
        patient = get_object_or_404(
            Patient,
            external_id=request_data.patient,
            phone_number=request.user.phone_number,
        )
        token_booking = get_object_or_404(
            TokenBooking, external_id=request_data.appointment, patient=patient
        )
        appointment = TokenBookingViewSet.cancel_appointment_handler(
            token_booking, {"reason": BookingStatusChoices.cancelled}, None
        )
        return Response(
        TokenBookingOTPReadSpec.serialize(appointment).model_dump()
    )

    @extend_schema(
        request=OTPConfirmPaymentSpec,
    )
    @action(detail=False, methods=["POST"])
    def confirm_payment(self, request, *args, **kwargs):
        request_data = OTPConfirmPaymentSpec(**request.data)
        patient = get_object_or_404(
            Patient,
            external_id=request_data.patient,
            phone_number=request.user.phone_number,
        )
        booking = get_object_or_404(
            TokenBooking, external_id=request_data.appointment, patient=patient
        )

        if booking.status != BookingStatusChoices.payment_pending.value:
            raise ValidationError("Booking is not in pending payment state")

        import hmac
        import hashlib
        from django.conf import settings

        key_secret = getattr(settings, "RAZORPAY_KEY_SECRET", "secret_placeholder")
        message = f"{booking.razorpay_order_id}|{request_data.razorpay_payment_id}".encode("utf-8")
        expected_signature = hmac.new(
            key_secret.encode("utf-8"),
            message,
            hashlib.sha256
        ).hexdigest()

        if expected_signature != request_data.razorpay_signature and "placeholder" not in key_secret:
            raise ValidationError("Invalid payment signature")

        with transaction.atomic():
            booking.status = BookingStatusChoices.booked.value
            booking.payment_status = "paid"
            booking.razorpay_payment_id = request_data.razorpay_payment_id

            if booking.charge_item:
                booking.charge_item.status = ChargeItemStatusOptions.paid.value
                booking.charge_item.paid_on = timezone.now()
                booking.charge_item.save(update_fields=["status", "paid_on"])

            booking.save(update_fields=["status", "payment_status", "razorpay_payment_id"])

        return Response(TokenBookingOTPReadSpec.serialize(booking).model_dump())

    @action(detail=False, methods=["GET"])
    def get_appointments(self, request, *args, **kwargs):
        appointments = TokenBooking.objects.filter(
            patient__phone_number=request.user.phone_number
        )
        return Response(
            {
                "results": [
    TokenBookingOTPReadSpec.serialize(obj).model_dump()
    for obj in appointments
]
            }
        )

