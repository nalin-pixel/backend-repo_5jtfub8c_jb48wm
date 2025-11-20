import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from database import db, create_document, get_documents

app = FastAPI(title="Food Delivery MVP API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------
# Utility serializers
# -----------------------

def serialize_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return doc
    d = dict(doc)
    _id = d.pop("_id", None)
    if _id is not None:
        d["id"] = str(_id)
    # Convert datetimes to isoformat
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


def serialize_list(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [serialize_doc(i) for i in items]


# -----------------------
# Schemas (minimal MVP)
# -----------------------

class Dish(BaseModel):
    restaurant_id: str
    name: str
    description: Optional[str] = None
    price_cents: int = Field(ge=0)
    image_url: Optional[str] = None
    is_available: bool = True


class Restaurant(BaseModel):
    name: str
    description: Optional[str] = None
    cover_url: Optional[str] = None
    cuisine: Optional[str] = None
    rating_avg: float = 4.7
    rating_count: int = 100
    delivery_fee_cents: int = 299
    eta_minutes: int = 25


class CartItem(BaseModel):
    item_id: str  # generated client/server side
    restaurant_id: str
    dish_id: str
    name: str
    quantity: int = Field(ge=1)
    price_cents: int
    image_url: Optional[str] = None


class Cart(BaseModel):
    user_id: str
    status: str = "active"  # active | checked_out | abandoned
    items: List[CartItem] = []
    subtotal_cents: int = 0
    delivery_fee_cents: int = 0
    total_cents: int = 0


class CheckoutRequest(BaseModel):
    address: str
    payment_method: str = Field(description="card | wallet | cod")
    tip_cents: int = 0
    notes: Optional[str] = None


# -----------------------
# Seed data if empty
# -----------------------

@app.on_event("startup")
def seed_data():
    if db is None:
        return
    # Restaurants
    if db["restaurant"].count_documents({}) == 0:
        samples = [
            Restaurant(
                name="UrbanBite Sushi",
                description="Fresh sushi and bowls",
                cover_url="https://images.unsplash.com/photo-1546069901-ba9599a7e63c",
                cuisine="Japanese",
                delivery_fee_cents=199,
                eta_minutes=20,
            ).model_dump(),
            Restaurant(
                name="ZestRun Pizza",
                description="Wood-fired pizzas & salads",
                cover_url="https://images.unsplash.com/photo-1548365328-9f547fb09501",
                cuisine="Italian",
                delivery_fee_cents=249,
                eta_minutes=30,
            ).model_dump(),
            Restaurant(
                name="Spice Lane",
                description="Curries, biryanis, and tandoor",
                cover_url="https://images.unsplash.com/photo-1604908554191-7a5d1f6e2fa5",
                cuisine="Indian",
                delivery_fee_cents=299,
                eta_minutes=35,
            ).model_dump(),
        ]
        for r in samples:
            r["created_at"] = datetime.now(timezone.utc)
            r["updated_at"] = datetime.now(timezone.utc)
            db["restaurant"].insert_one(r)

    # Dishes
    if db["dish"].count_documents({}) == 0:
        restaurants = list(db["restaurant"].find({}))
        if restaurants:
            def rid(i: int) -> str:
                from bson import ObjectId
                return str(restaurants[i]["_id"]) if i < len(restaurants) else str(restaurants[0]["_id"])

            dishes = [
                Dish(restaurant_id=rid(0), name="Salmon Nigiri (2pc)", description="Hand-pressed sushi with fresh salmon", price_cents=550, image_url="https://images.unsplash.com/photo-1553621042-f6e147245754").model_dump(),
                Dish(restaurant_id=rid(0), name="Spicy Tuna Roll", description="8pc roll with sriracha mayo", price_cents=1200, image_url="https://images.unsplash.com/photo-1562158070-5bf2a746b01b").model_dump(),
                Dish(restaurant_id=rid(1), name="Margherita Pizza", description="Tomato, mozzarella, basil", price_cents=1299, image_url="https://images.unsplash.com/photo-1548365328-9f547fb09501").model_dump(),
                Dish(restaurant_id=rid(1), name="Pepperoni Pizza", description="Classic pepperoni", price_cents=1499, image_url="https://images.unsplash.com/photo-1542281286-9e0a16bb7366").model_dump(),
                Dish(restaurant_id=rid(2), name="Chicken Tikka Masala", description="Creamy tomato sauce", price_cents=1399, image_url="https://images.unsplash.com/photo-1604908177093-9e1b85bbff1a").model_dump(),
                Dish(restaurant_id=rid(2), name="Veg Biryani", description="Fragrant basmati with veggies", price_cents=1199, image_url="https://images.unsplash.com/photo-1633945274405-68c1373d4b55").model_dump(),
            ]
            for d in dishes:
                d["created_at"] = datetime.now(timezone.utc)
                d["updated_at"] = datetime.now(timezone.utc)
                db["dish"].insert_one(d)


# -----------------------
# Basic endpoints
# -----------------------

@app.get("/")
def read_root():
    return {"message": "Food Delivery Backend is running"}


@app.get("/restaurants")
def list_restaurants():
    items = list(db["restaurant"].find({}).limit(50)) if db else []
    return serialize_list(items)


@app.get("/restaurants/{restaurant_id}")
def get_restaurant(restaurant_id: str):
    from bson import ObjectId
    r = db["restaurant"].find_one({"_id": ObjectId(restaurant_id)}) if db else None
    if not r:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    return serialize_doc(r)


@app.get("/restaurants/{restaurant_id}/menu")
def get_menu(restaurant_id: str):
    items = list(db["dish"].find({"restaurant_id": restaurant_id})) if db else []
    return serialize_list(items)


# -----------------------
# Cart endpoints (user via header)
# -----------------------


@app.post("/carts")
def create_cart(x_user_id: Optional[str] = Header(default=None, convert_underscores=False)):
    user_id = x_user_id or "demo-user"
    # Ensure only one active cart
    existing = db["cart"].find_one({"user_id": user_id, "status": "active"}) if db else None
    if existing:
        return serialize_doc(existing)
    cart = Cart(user_id=user_id).model_dump()
    cart_id = create_document("cart", cart)
    created = db["cart"].find_one({"_id": db["cart"]._BaseObjectId(cart_id)}) if False else db["cart"].find_one({"_id": db["cart"].find_one({"_id": {"$exists": True}})["_id"]})
    # Safer fetch by user
    created = db["cart"].find_one({"user_id": user_id, "status": "active"})
    return serialize_doc(created)


@app.get("/carts/active")
def get_active_cart(x_user_id: Optional[str] = Header(default=None, convert_underscores=False)):
    user_id = x_user_id or "demo-user"
    cart = db["cart"].find_one({"user_id": user_id, "status": "active"}) if db else None
    if not cart:
        # auto-create
        return create_cart(x_user_id=user_id)
    return serialize_doc(cart)


class AddItemRequest(BaseModel):
    restaurant_id: str
    dish_id: str
    quantity: int = 1


@app.post("/carts/items")
def add_item(payload: AddItemRequest, x_user_id: Optional[str] = Header(default=None, convert_underscores=False)):
    user_id = x_user_id or "demo-user"
    cart = db["cart"].find_one({"user_id": user_id, "status": "active"})
    if not cart:
        cart = Cart(user_id=user_id).model_dump()
        _ = create_document("cart", cart)
        cart = db["cart"].find_one({"user_id": user_id, "status": "active"})

    # fetch dish snapshot
    from bson import ObjectId
    dish = db["dish"].find_one({"_id": ObjectId(payload.dish_id)})
    if not dish:
        raise HTTPException(status_code=404, detail="Dish not found")

    import uuid
    item = CartItem(
        item_id=str(uuid.uuid4()),
        restaurant_id=payload.restaurant_id,
        dish_id=payload.dish_id,
        name=dish.get("name"),
        quantity=max(1, payload.quantity),
        price_cents=int(dish.get("price_cents", 0)),
        image_url=dish.get("image_url"),
    ).model_dump()

    items = list(cart.get("items", []))
    # merge if same dish
    merged = False
    for it in items:
        if it["dish_id"] == item["dish_id"]:
            it["quantity"] += item["quantity"]
            merged = True
            break
    if not merged:
        items.append(item)

    subtotal = sum(i["price_cents"] * i["quantity"] for i in items)
    # simple delivery fee rule: highest restaurant fee among items
    rids = list({i["restaurant_id"] for i in items})
    fees = []
    for rid in rids:
        try:
            rr = db["restaurant"].find_one({"_id": ObjectId(rid)})
            if rr:
                fees.append(int(rr.get("delivery_fee_cents", 0)))
        except Exception:
            pass
    delivery_fee = max(fees) if fees else 0
    total = subtotal + delivery_fee

    db["cart"].update_one({"_id": cart["_id"]}, {"$set": {
        "items": items,
        "subtotal_cents": subtotal,
        "delivery_fee_cents": delivery_fee,
        "total_cents": total,
        "updated_at": datetime.now(timezone.utc)
    }})

    updated = db["cart"].find_one({"_id": cart["_id"]})
    return serialize_doc(updated)


class UpdateItemRequest(BaseModel):
    quantity: int


@app.patch("/carts/items/{item_id}")
def update_item(item_id: str, payload: UpdateItemRequest, x_user_id: Optional[str] = Header(default=None, convert_underscores=False)):
    user_id = x_user_id or "demo-user"
    cart = db["cart"].find_one({"user_id": user_id, "status": "active"})
    if not cart:
        raise HTTPException(status_code=404, detail="No active cart")
    items = list(cart.get("items", []))
    found = False
    for it in items:
        if it["item_id"] == item_id:
            found = True
            it["quantity"] = max(0, payload.quantity)
    if not found:
        raise HTTPException(status_code=404, detail="Item not found")
    items = [i for i in items if i["quantity"] > 0]
    subtotal = sum(i["price_cents"] * i["quantity"] for i in items)
    # recompute fee
    from bson import ObjectId
    rids = list({i["restaurant_id"] for i in items})
    fees = []
    for rid in rids:
        try:
            rr = db["restaurant"].find_one({"_id": ObjectId(rid)})
            if rr:
                fees.append(int(rr.get("delivery_fee_cents", 0)))
        except Exception:
            pass
    delivery_fee = max(fees) if fees else 0
    total = subtotal + delivery_fee

    db["cart"].update_one({"_id": cart["_id"]}, {"$set": {
        "items": items,
        "subtotal_cents": subtotal,
        "delivery_fee_cents": delivery_fee,
        "total_cents": total,
        "updated_at": datetime.now(timezone.utc)
    }})
    updated = db["cart"].find_one({"_id": cart["_id"]})
    return serialize_doc(updated)


# -----------------------
# Checkout / Orders
# -----------------------


@app.post("/checkout")
def checkout(payload: CheckoutRequest, x_user_id: Optional[str] = Header(default=None, convert_underscores=False)):
    user_id = x_user_id or "demo-user"
    cart = db["cart"].find_one({"user_id": user_id, "status": "active"})
    if not cart or not cart.get("items"):
        raise HTTPException(status_code=400, detail="Cart is empty")

    order = {
        "user_id": user_id,
        "items": cart.get("items", []),
        "address": payload.address,
        "payment_method": payload.payment_method,
        "tip_cents": int(payload.tip_cents or 0),
        "notes": payload.notes,
        "subtotal_cents": cart.get("subtotal_cents", 0),
        "delivery_fee_cents": cart.get("delivery_fee_cents", 0),
        "total_cents": cart.get("total_cents", 0) + int(payload.tip_cents or 0),
        "status": "confirmed",
        "placed_at": datetime.now(timezone.utc),
        "eta_minutes": 30,
    }
    order_id = create_document("order", order)

    # close cart
    db["cart"].update_one({"_id": cart["_id"]}, {"$set": {"status": "checked_out", "updated_at": datetime.now(timezone.utc)}})

    created = db["order"].find_one({"_id": db["order"].find_one({})["_id"]})  # placeholder fetch
    from bson import ObjectId
    created = db["order"].find_one({"_id": ObjectId(order_id)})
    return serialize_doc(created)


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    from bson import ObjectId
    o = db["order"].find_one({"_id": ObjectId(order_id)}) if db else None
    if not o:
        raise HTTPException(status_code=404, detail="Order not found")
    return serialize_doc(o)


@app.get("/orders/{order_id}/track")
def track_order(order_id: str):
    from bson import ObjectId
    o = db["order"].find_one({"_id": ObjectId(order_id)}) if db else None
    if not o:
        raise HTTPException(status_code=404, detail="Order not found")

    # Simulate state progression over time since placed_at
    placed = o.get("placed_at") or datetime.now(timezone.utc)
    now = datetime.now(timezone.utc)
    elapsed = (now - placed).total_seconds()
    status = "confirmed"
    steps = [
        (0, "confirmed"),
        (60, "preparing"),
        (8 * 60, "picked_up"),
        (15 * 60, "en_route"),
        (25 * 60, "delivered"),
    ]
    for t, s in steps:
        if elapsed >= t:
            status = s
    # update if changed
    if status != o.get("status"):
        db["order"].update_one({"_id": o["_id"]}, {"$set": {"status": status, "updated_at": now}})
        o["status"] = status
        o["updated_at"] = now
    data = serialize_doc(o)
    data["elapsed_seconds"] = int(elapsed)
    return data


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = os.getenv("DATABASE_NAME") or "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"

    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
