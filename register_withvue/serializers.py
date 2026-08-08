from rest_framework import serializers
from .models import Student, Group, Teacher, Course, CourseLevel, News, Room


class TeacherMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Teacher
        fields = ["id", "name", "phone", "is_senior"]


class StudentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Student
        fields = ["id", "name", "surname", "phone", "teacher"]


class GroupSerializer(serializers.ModelSerializer):
    course_name = serializers.CharField(source="course.name", read_only=True)
    # Guruhning haqiqiy narxi: daraja narxi bo'lsa u, aks holda kursniki.
    # Ilgari bu to'g'ridan-to'g'ri kursdan olinardi — daraja qo'shilgach
    # ro'yxatda ko'ringan narx bilan hisoblangan to'lov farq qilardi.
    monthly_fee = serializers.SerializerMethodField()
    course_monthly_fee = serializers.IntegerField(
        source="course.monthly_fee", read_only=True, default=0
    )
    level_name = serializers.CharField(source="level.name", read_only=True, default="")
    room_name = serializers.SerializerMethodField()

    students_count = serializers.SerializerMethodField()
    students = StudentSerializer(many=True, read_only=True)
    teacher = TeacherMiniSerializer(read_only=True)  # ✅ endi to'liq obyekt qaytaradi

    class Meta:
        model = Group
        fields = "__all__"

    def get_students_count(self, obj):
        return obj.students.count()

    def get_monthly_fee(self, obj):
        return obj.effective_monthly_fee

    def get_room_name(self, obj):
        return obj.room_ref.name if obj.room_ref_id else (obj.room or "")


class CourseLevelSerializer(serializers.ModelSerializer):
    # Darajada narx kiritilmagan bo'lsa kursniki amal qiladi — frontend
    # "qaysi narx ishlaydi" degan savolga javobni tayyor olsin
    effective_fee = serializers.IntegerField(read_only=True)
    groups_count = serializers.SerializerMethodField()

    class Meta:
        model = CourseLevel
        fields = [
            "id",
            "course",
            "name",
            "order",
            "monthly_fee",
            "effective_fee",
            "note",
            "groups_count",
        ]

    def get_groups_count(self, obj):
        return obj.groups.count()


class CourseSerializer(serializers.ModelSerializer):
    groups_count = serializers.SerializerMethodField()
    levels = CourseLevelSerializer(many=True, read_only=True)
    levels_count = serializers.SerializerMethodField()

    class Meta:
        model = Course
        fields = [
            "id",
            "name",
            "monthly_fee",
            "groups_count",
            "levels",
            "levels_count",
        ]

    def get_groups_count(self, obj):
        return obj.groups.count()

    def get_levels_count(self, obj):
        return obj.levels.count()


class RoomSerializer(serializers.ModelSerializer):
    groups_count = serializers.SerializerMethodField()

    class Meta:
        model = Room
        fields = ["id", "name", "capacity", "note", "is_active", "groups_count"]

    def get_groups_count(self, obj):
        return obj.groups.count()


class NewsSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(
        source="created_by.get_full_name", read_only=True, default=""
    )

    class Meta:
        model = News
        fields = [
            "id",
            "title",
            "content",
            "priority",
            "is_active",
            "created_by_name",
            "created_at",
            "updated_at",
            "expires_at",
        ]
        read_only_fields = ["created_by_name", "created_at", "updated_at"]
