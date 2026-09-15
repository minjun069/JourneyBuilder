from rest_framework import serializers
from .models import Report


# 공통 display 필드 - 부모 Serializer
class ReportBaseSerializer(serializers.ModelSerializer):
    report_type_display = serializers.CharField(
        source='get_report_type_display',
        read_only=True
    )
    status_display = serializers.CharField(
        source='get_status_display',
        read_only=True
    )

    class Meta:
        model = Report


# Dashboard와 Presentation이 한 번의 인증된 조회로 같은 결과 집합을 소비한다.
class ReportListSerializer(ReportBaseSerializer):

    class Meta(ReportBaseSerializer.Meta):
        fields = [
            'id',
            'session_id',
            'report_type',
            'report_type_display',
            'version',
            'is_latest',
            'status',
            'status_display',
            'content',
            'pdf_url',
            'generated_at',
            'created_at',
        ]
        read_only_fields = tuple(fields)


# 보고서 상세 조회 — content 전체 포함
class ReportSerializer(ReportBaseSerializer):

    class Meta(ReportBaseSerializer.Meta):
        fields = [
            'id',
            'session_id',
            'report_type',
            'report_type_display',
            'version',
            'is_latest',
            'status',
            'status_display',
            'content',
            'pdf_url',
            'generated_at',
            'created_at',
        ]
        read_only_fields = tuple(fields)
