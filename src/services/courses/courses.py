import json
import os
from typing import List
from uuid import uuid4
from pydantic import BaseModel
from src.services.courses.elements import ElementInDB
from src.services.uploads import upload_thumbnail
from src.services.users import PublicUser, User
from src.services.database import create_config_collection, check_database, create_database, learnhouseDB
from src.services.security import *
from fastapi import FastAPI, HTTPException, status, Request, Response, BackgroundTasks, UploadFile, File
from datetime import datetime

#### Classes ####################################################


class Course(BaseModel):
    name: str
    mini_description: str
    description: str
    learnings: List[str]
    thumbnail: str
    public: bool
    chapters: List[str]
    org_id: str


class CourseInDB(Course):
    course_id: str
    creationDate: str
    updateDate: str
    authors: List[str]


# TODO : wow terrible, fix this
# those models need to be available only in the chapters service
class CourseChapter(BaseModel):
    name: str
    description: str
    elements: list


class CourseChapterInDB(CourseChapter):
    coursechapter_id: str
    course_id: str
    creationDate: str
    updateDate: str

#### Classes ####################################################

# TODO : Add courses photo & cover upload and delete


####################################################
# CRUD
####################################################

async def get_course(course_id: str, current_user: PublicUser):
    await check_database()
    courses = learnhouseDB["courses"]

    course = courses.find_one({"course_id": course_id})

    # verify course rights
    await verify_rights(course_id, current_user, "read")

    if not course:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Course does not exist")

    course = Course(**course)
    return course


async def get_course_meta(course_id: str, current_user: PublicUser):
    await check_database()
    courses = learnhouseDB["courses"]
    coursechapters = learnhouseDB["coursechapters"]
    course = courses.find_one({"course_id": course_id})
    elements = learnhouseDB["elements"]


    # verify course rights
    await verify_rights(course_id, current_user, "read")

    if not course:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Course does not exist")


    coursechapters = coursechapters.find(
        {"course_id": course_id}).sort("name", 1)

    # elements
    coursechapter_elementIds_global = []

    # chapters
    chapters = {}
    for coursechapter in coursechapters:
        coursechapter = CourseChapterInDB(**coursechapter)
        coursechapter_elementIds = []

        for element in coursechapter.elements:
            coursechapter_elementIds.append(element)
            coursechapter_elementIds_global.append(element)

        chapters[coursechapter.coursechapter_id] = {
            "id": coursechapter.coursechapter_id, "name": coursechapter.name,  "elementIds": coursechapter_elementIds
        }

    # elements
    elements_list = {}
    for element in elements.find({"element_id": {"$in": coursechapter_elementIds_global}}):
        element = ElementInDB(**element)
        elements_list[element.element_id] = {
            "id": element.element_id, "name": element.name, "type": element.type, "content": element.content
        }

    chapters_list_with_elements = []
    for chapter in chapters:
        chapters_list_with_elements.append(
            {"id": chapters[chapter]["id"], "name": chapters[chapter]["name"], "elements": [elements_list[element] for element in chapters[chapter]["elementIds"]]})
    course = Course(**course)
    return {
        "course": course,
        "chapters": chapters_list_with_elements,
    }


async def create_course(course_object: Course, org_id: str, current_user: PublicUser, thumbnail_file: UploadFile | None = None):
    await check_database()
    courses = learnhouseDB["courses"]

    # generate course_id with uuid4
    course_id = str(f"course_{uuid4()}")

    # TODO(fix) : the implementation here is clearly not the best one (this entire function)
    course_object.org_id = org_id
    hasRoleRights = await verify_user_rights_with_roles("create", current_user.user_id, course_id)

    if not hasRoleRights:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Roles : Insufficient rights to perform this action")

    if thumbnail_file:
        name_in_disk = f"{course_id}_thumbnail_{uuid4()}.{thumbnail_file.filename.split('.')[-1]}"
        await upload_thumbnail(thumbnail_file, name_in_disk)
        course_object.thumbnail = name_in_disk

    course = CourseInDB(course_id=course_id, authors=[
        current_user.user_id], creationDate=str(datetime.now()), updateDate=str(datetime.now()), **course_object.dict())

    course_in_db = courses.insert_one(course.dict())

    if not course_in_db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Unavailable database")

    return course.dict()


async def update_course_thumbnail(course_id: str, current_user: PublicUser, thumbnail_file: UploadFile | None = None):
    await check_database()

    # verify course rights
    await verify_rights(course_id, current_user, "update")

    courses = learnhouseDB["courses"]

    course = courses.find_one({"course_id": course_id})
    # TODO(fix) : the implementation here is clearly not the best one
    if course:
        creationDate = course["creationDate"]
        authors = course["authors"]
        if thumbnail_file:
            name_in_disk = f"{course_id}_thumbnail_{uuid4()}.{thumbnail_file.filename.split('.')[-1]}"
            course = Course(**course).copy(update={"thumbnail": name_in_disk})
            await upload_thumbnail(thumbnail_file, name_in_disk)

            updated_course = CourseInDB(course_id=course_id, creationDate=creationDate,
                                        authors=authors, updateDate=str(datetime.now()), **course.dict())

            courses.update_one({"course_id": course_id}, {
                "$set": updated_course.dict()})

            return CourseInDB(**updated_course.dict())

    else:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Course does not exist")


async def update_course(course_object: Course, course_id: str, current_user: PublicUser):
    await check_database()

    # verify course rights
    await verify_rights(course_id, current_user, "update")

    courses = learnhouseDB["courses"]

    course = courses.find_one({"course_id": course_id})

    if course:
        creationDate = course["creationDate"]
        authors = course["authors"]

        # get today's date
        datetime_object = datetime.now()

        updated_course = CourseInDB(
            course_id=course_id, creationDate=creationDate, authors=authors, updateDate=str(datetime_object), **course_object.dict())

        courses.update_one({"course_id": course_id}, {
            "$set": updated_course.dict()})

        return CourseInDB(**updated_course.dict())

    else:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Course does not exist")


async def delete_course(course_id: str, current_user: PublicUser):
    await check_database()

    # verify course rights
    await verify_rights(course_id, current_user, "delete")

    courses = learnhouseDB["courses"]

    course = courses.find_one({"course_id": course_id})

    if not course:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Course does not exist")

    isDeleted = courses.delete_one({"course_id": course_id})

    if isDeleted:
        return {"detail": "Course deleted"}
    else:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Unavailable database")

####################################################
# Misc
####################################################


async def get_courses(page: int = 1, limit: int = 10, org_id: str | None = None):
    await check_database()
    courses = learnhouseDB["courses"]
    # TODO : Get only courses that user is admin/has roles of
    # get all courses from database
    all_courses = courses.find({"org_id": org_id}).sort(
        "name", 1).skip(10 * (page - 1)).limit(limit)

    return [json.loads(json.dumps(course, default=str)) for course in all_courses]


#### Security ####################################################


async def verify_rights(course_id: str, current_user: PublicUser, action: str):
    await check_database()
    courses = learnhouseDB["courses"]

    course = courses.find_one({"course_id": course_id})

    if not course:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Course/CourseChapter does not exist")

    hasRoleRights = await verify_user_rights_with_roles(action, current_user.user_id, course_id)
    isAuthor = current_user.user_id in course["authors"]

    if not hasRoleRights and not isAuthor:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Roles/Ownership : Insufficient rights to perform this action")

    return True

#### Security ####################################################