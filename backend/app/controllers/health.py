from fastapi import APIRouter

router = APIRouter()


@router.get("/")
def read_root():
    return {"status": "alive", "message": "Thermotree API is running!"}
