from sqlalchemy import create_engine, select, insert, func, update
from sqlalchemy.orm import Session
from sqlalchemy.exc import NoResultFound
from typing import Optional
import os
from utils.llc_datetime import get_first_day_of_previous_month, get_first_day_of_current_month, get_today
import database_api_layer.models as db
from utils.logger import Logger
from database_api_layer.db_utils import get_min_available_id, get_multiple_available_id
from sqlalchemy.exc import SQLAlchemyError
import asyncio
import traceback

class DatabaseAPILayer:
  engine = None
  def __init__(self, client):
    dbschema = os.getenv('POSTGRESQL_SCHEMA')
    self.engine = create_engine(
      os.getenv('POSTGRESQL_CRED'), 
      connect_args={'options': '-csearch_path={}'.format(dbschema)}, echo=True)
    self.client = client
    self.logger = Logger(client)
  
  # Generalize all session commits behavior
  async def __commit(self, session, context):
    result = None
    try:
      session.commit()
    except Exception as e:
      await self.logger.on_db_update(False, context, e)
      result = False
    else:
      await self.logger.on_db_update(True, context, "")
      result = True
    return result

  # this will be invoked by any function that needs update of score
  # make sure this will be in the same commit as other changes
  # this do not invoke session.commit
  def __update_user_score(self, session, userId, score):
    daily_obj_query = select(db.DailyObject)\
      .where(db.DailyObject.generatedDate == get_today())
    daily_obj = session.execute(daily_obj_query).one()
    user_daily_query = select(db.UserDailyObject)\
      .where(db.UserDailyObject.userId == userId)\
      .where(db.UserDailyObject.dailyObjectId == daily_obj.DailyObject.id)
    user_daily_obj = session.execute(user_daily_query).one_or_none()
    if user_daily_obj == None:
      new_obj = db.UserDailyObject(
        id=get_min_available_id(session, db.UserDailyObject),
        userId=userId,
        dailyObjectId=daily_obj.DailyObject.id,
        scoreEarned=0
      )
      session.add(new_obj)
    else:
      daily_id = daily_obj.DailyObject.id
      update_query = update(db.UserDailyObject)\
        .where(db.UserDailyObject.userId == userId)\
        .where(db.UserDailyObject.dailyObjectId == daily_id)\
        .values(scoreEarned = user_daily_obj.UserDailyObject.scoreEarned + score)
      session.execute(update_query)

    first_day_of_month = get_first_day_of_current_month()
    user_monthly_query = select(db.UserMonthlyObject)\
      .where(db.UserMonthlyObject.userId == userId)\
      .where(db.UserMonthlyObject.firstDayOfMonth == first_day_of_month)
    user_monthly_obj = session.execute(user_monthly_query).one_or_none()
    if (user_monthly_obj == None):
      new_obj = db.UserMonthlyObject(
        id=get_min_available_id(session, db.UserMonthlyObject),
        userId=userId,
        firstDayOfMonth=first_day_of_month,
        scoreEarned=score
      )
      session.add(new_obj)
      result = new_obj.id
    else:
      update_query = update(db.UserMonthlyObject)\
        .where(db.UserMonthlyObject.userId == userId)\
        .where(db.UserMonthlyObject.firstDayOfMonth == first_day_of_month)\
        .values(scoreEarned = user_monthly_obj.UserMonthlyObject.scoreEarned + score)
      session.execute(update_query)
    return
  
  def __create_submission(self, session, userId, problemId, submissionId):
    new_obj = db.UserSolvedProblem(
      id=get_min_available_id(session, db.UserSolvedProblem),
      userId=userId,
      problemId=problemId,
      submissionId=submissionId
    )
    session.add(new_obj)
    return

  # Missing infos in SQL comparing to previous features:
  # - AC Count & Rate
  # - Like & Dislike
  # Infos to be added:
  # - # Comm members solves
  def read_latest_daily(self):
    query = select(db.DailyObject).order_by(db.DailyObject.id.desc()).limit(1)
    result = None
    with Session(self.engine) as session:
      daily = session.scalars(query).one()
      problem = daily.problem
      result = daily.__dict__
      result["problem"] = problem.__dict__
      result["problem"]["topics"] = list(map(lambda topic: topic.topicName, problem.topics))
    return result

  # We disable getting data from random user for now.
  def read_profile(self, memberDiscordId):
    query = select(db.User).where(db.User.discordId == memberDiscordId)
    result = None
    with Session(self.engine) as session:
      profile = session.scalars(query).one_or_none()
      if profile == None:
        return None
      # try to optimize using sql filter next time! filter by python is very unoptimized!
      daily_objs = list(filter(lambda obj: obj.dailyObject.generatedDate == get_today(), profile.userDailyObjects))

      result = profile.__dict__
      if len(daily_objs) == 0:
        result['daily'] = {
          'scoreEarned': 0,
          'solvedDaily': 0,
          'solvedEasy': 0,
          'solvedMedium': 0,
          'solvedHard': 0,
          'rank': "N/A"
        }
      else:
        result['daily'] = daily_objs[0].__dict__
        result['daily']['rank'] = "N/A"

      monthly_objs = list(filter(lambda obj: obj.firstDayOfMonth == get_first_day_of_current_month(), profile.userMonthlyObjects))
      if len(monthly_objs) == 0:
        result['monthly'] = {
          'scoreEarned': 0,
          'rank': "N/A"
        }
      else:
        result['monthly'] = monthly_objs[0].__dict__
        result['monthly']['rank'] = "N/A"

    result['link'] = f"https://leetcode.com/{profile.leetcodeUsername}"
    return result

  # Currently, just return user with a monthly object
  def read_current_month_leaderboard(self):
    query = select(db.UserMonthlyObject, db.User).join_from(
      db.UserMonthlyObject, db.User).where(
      db.UserMonthlyObject.firstDayOfMonth == get_first_day_of_current_month()
    ).order_by(db.UserMonthlyObject.scoreEarned.desc())
    result = []
    with Session(self.engine) as session:
      queryResult = session.execute(query).all()
      for res in queryResult:
        result.append({**res.User.__dict__, **res.UserMonthlyObject.__dict__})
    return result
  
  def read_last_month_leaderboard(self):
    query = select(db.UserMonthlyObject, db.User).join_from(
      db.UserMonthlyObject, db.User).where(
      db.UserMonthlyObject.firstDayOfMonth == get_first_day_of_previous_month()
    ).order_by(db.UserMonthlyObject.scoreEarned.desc())
    result = []
    with Session(self.engine) as session:
      queryResult = session.execute(query).all()
      for res in queryResult:
        result.append({**res.User.__dict__, **res.UserMonthlyObject.__dict__})
    return result
  
  # Desc: return one random problem, with difficulty filter + tags filter
  def read_gimme(self, difficulty, tags_1, tags_2, premium = False):
    return {}

  # Desc: update to DB and send a log
  def update_score(self, memberDiscordId, delta):
    return {}

  # Can we split this fn into 2?
  async def create_user(self, user_obj):
    problems = user_obj['userSolvedProblems']
    problems_query = select(db.Problem).filter(db.Problem.titleSlug.in_(problems))
    with Session(self.engine, autoflush=False) as session:
      queryResult = session.execute(problems_query).all()
      min_available_user_id = get_min_available_id(session, db.User)
      new_user = db.User(
        id=min_available_user_id,
        discordId=user_obj['discordId'],
        leetcodeUsername=user_obj['leetcodeUsername'],
        mostRecentSubId=user_obj['mostRecentSubId'],
        userSolvedProblems=[]
      )
      available_solved_problem_ids = get_multiple_available_id(session, db.UserSolvedProblem, len(queryResult))
      idx = 0
      for problem in queryResult:
        user_solved_problem = db.UserSolvedProblem(
          id=available_solved_problem_ids[idx],
          problemId=problem.Problem.id,
          submissionId=-1
        )
        new_user.userSolvedProblems.append(user_solved_problem)
        idx += 1
      session.add(new_user)
      result = new_user.id
      await self.__commit(session, "User")

    return { "id": result }

  async def create_monthly_object(self, userId, firstDayOfMonth):
    with Session(self.engine, autoflush=False) as session:
      new_obj = db.UserMonthlyObject(
        id=get_min_available_id(session, db.UserMonthlyObject),
        userId=userId,
        firstDayOfMonth=firstDayOfMonth,
        scoreEarned=0
      )
      session.add(new_obj)
      result = new_obj.id
      await self.__commit(session, f"UserMonthlyObject<id:{result}>")

    return { "id": result }

  def read_problems_all(self):
    query = select(db.Problem).order_by(db.Problem.id)
    result = []
    with Session(self.engine) as session:
      queryResult = session.execute(query).all()
      for res in queryResult:
        result.append(res.Problem.__dict__)
    return result
  
  def read_problem_from_slug(self, titleSlug):
    query = select(db.Problem).where(db.Problem.titleSlug == titleSlug)
    with Session(self.engine) as session:
      queryResult = session.execute(query).one_or_none()
      if queryResult == None:
        return None

      result = queryResult.Problem.__dict__
    return result

  async def create_problem(self, problem):
    topic_list = list(map(lambda topic: topic['name'], problem['topicTags']))
    query = select(db.Topic).filter(db.Topic.topicName.in_(topic_list))
    with Session(self.engine, autoflush=False) as session:
      queryResult = session.execute(query).all()
      print(queryResult)
      new_obj = db.Problem(
        id=get_min_available_id(session, db.Problem),
        title=problem['title'],
        titleSlug=problem['titleSlug'],
        difficulty=problem['difficulty'],
        isPremium=problem['paidOnly'],
        topics=[row.Topic for row in queryResult]
      )

      session.add(new_obj)
      result = new_obj.id
      await self.__commit(session, f"Problem<id:{result}>")

    return { "id": result }
  
  async def register_new_submission(self, userId, problemId, submissionId):
    with Session(self.engine, autoflush=False) as session:
      self.__create_submission(session, userId, problemId, submissionId)
      self.__update_user_score(session, userId, 2)
      await self.__commit(session, f"UserSolvedProblem<userId={userId},problemId={problemId}>, Score<ScoreEarned={2}, SubmissionId={submissionId}>")
    return

## Features to be refactoring
# tasks
# gimme

# onboard info - need database
#

# qa
