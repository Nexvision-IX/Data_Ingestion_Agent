import time
import schedule

from ingestion.master_ingestion import run_ingestion

# -----------------------------------
# JOB
# -----------------------------------

def ingestion_job():

    print("\n==============================")
    print("STARTING INGESTION JOB")
    print("==============================\n")

    run_ingestion()

    print("\n==============================")
    print("INGESTION JOB COMPLETED")
    print("==============================\n")
    
# -----------------------------------
# RUN ONCE IMMEDIATELY
# -----------------------------------

ingestion_job()

# -----------------------------------
# SCHEDULE EVERY 5 MINUTES
# -----------------------------------

schedule.every(5).minutes.do(
    ingestion_job
)

print("Scheduler started...")
print("Ingestion will run every 5 minutes.\n")

# -----------------------------------
# KEEP PROCESS RUNNING
# -----------------------------------

while True:

    schedule.run_pending()

    time.sleep(1)