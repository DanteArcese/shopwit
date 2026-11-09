"""Category precision on real Open Prices titles (#88).

Each case is a short (title, brand) pair as it arrived from Open Prices, with the
category it must match, or None when it must stay out of every curated category.
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from normalize import load_categories
from normalize.match import match_category
from normalize.types import RawOffer

ROOT = Path(__file__).resolve().parents[2]
CATEGORIES = load_categories(ROOT / "categories")

MILK, COFFEE, EGGS = "dairy/milk", "pantry/coffee", "dairy/eggs"
BREAD, RICE, PASTA, FLOUR = "pantry/bread", "pantry/rice", "pantry/pasta", "pantry/flour"
PB, OIL, SUGAR = "pantry/peanut-butter", "pantry/cooking-oil", "pantry/sugar"
WATER, SPARKLING = "beverages/bottled-water", "beverages/sparkling-water"
OJ, SODA = "beverages/orange-juice", "beverages/soda"

CASES = [
    # Milk: plain dairy milk matches.
    ("A2/A2 4% Whole Milk Vitamin D", "Clover Sonoma", MILK),
    ("Ultra-pasteurized lactose free 2% reduced fat milk", "Clover", MILK),
    ("Protein Plus 2% Reduced Fat Ultra-Filtered Milk", "Organic Valley", MILK),
    ("PROTEIN Milk", "Lactaid", MILK),
    ("Organic Whole Omega-3 Milk", "Simply Nature", MILK),
    ("Organic Valley 0% Milk", "Organic Valley", MILK),
    ("Grade a vitamin d milk", None, MILK),
    ("Whole Homogenized milk", "South Mountain Creamery", MILK),
    # Milk: shakes, protein and coffee drinks.
    ("HIGH PROTEIN SHAKE", "MUSCLE MILK", None),
    ("MM42G", "Muscle Milk", None),
    ("Classic strawberry high protein milk shake", "Slate", None),
    ("Draft Latte Caramel Cold Brew with Milk", "La Colombe", None),
    ("ICED ESPRESSO & MILK BEVERAGE Salted Caramel", "DUNKIN' DOUBLE", None),
    ("Berry Milk Drink", "Barebells", None),
    # Milk: yogurt, kefir, cheese, buttermilk.
    ("Organic Whole Milk Honey Yogurt", "KALONA SUPER NATURAL", "dairy/yogurt"),
    ("Organic Kefir cultured lowfat milk", "Lifeway", None),
    ("Part Skim Milk Cheese", "Cacique Inc.", None),
    ("Cultured whole butter milk", "Fire Acre Farms", None),
    ("Butter Milk", None, None),
    # Milk: flavored and non-dairy "milks".
    ("Vanilla Ultra Filtered Milk", "Barebells", None),
    ("Strawberry Milk", "Spyt", None),
    ("Pistachio Milk", "TÁCHE", None),
    ("organic banana milk", "Mooala", None),
    ("Horchata Milk", "Moozy", None),
    ("DAIRY-FREE MILK", "ripple", None),
    ("Original Plant-Based Milk Kids", "Ripple Foods", None),
    ("Animal Free Dairy Milk", "Strive Freemilk", None),
    ("Choco milk powder drink mix", "Choco Milk", None),
    ("Original malted milk", "Nestlé", None),
    # Milk: teas, candy, desserts.
    ("Brown sugar bubble milk tea", "Rico Bubble Milk Tea", None),
    ("Organic Mother's Milk", "Traditional Medicinals", None),
    ("Milk Thistle Tea", None, None),
    ("Milk Duds Candy", "Milk Duds", None),
    ("Milk Pistachio Truffles", "Lindt Lindor", None),
    ("Choceur Creamy Milk", "Choceur", None),
    ("Feastables - Milk Crunch", "Feastables, MrBeast", None),
    ("Rye Crumb, Milk & Sea Salt", "Pump Street", None),
    ("Ben & Jerry's Milk and cookies pt", "Ben & Jerry's, Unilever", None),
    ("Fun Size Milk Way Bars", "Mars", None),
    # Coffee: beans and ground coffee match.
    ("Big Bang Medium Roast Ground Coffee", "Peet's Coffee", COFFEE),
    ("Dark Roast French Roast Whole Bean Coffee", "Peet's", COFFEE),
    ("Decaf Colombia Ground Coffee", "Signature Select", COFFEE),
    ("Pumpkin Spice Artificially Flavored Ground Coffee", "Dunkin'", COFFEE),
    ("Estilo Espresso Espresso Style Dark Roast Coffee", "Café Bustelo", COFFEE),
    ("Ground Espresso Coffee", "Illy", COFFEE),
    ("whole coffee beans", "Comfort", COFFEE),
    ("Starbucks Caramel Flavored Coffee", "Starbucks", COFFEE),
    ("Starbucks Iced Coffee Blend Medium Roast Ground Coffee 12 oz", "Starbucks", COFFEE),
    ("Toffee Crunch Ground Coffee", None, COFFEE),
    ("Dark Chocolate Flavored Ground Coffee", None, COFFEE),
    ("Morning Energy Blend Ground Coffee", None, COFFEE),
    # Coffee: ready-to-drink and energy drinks.
    ("Throne Sport Coffee Mocha Java", "Throne", None),
    ("ENERGY COFFEE BEVERAGE", "STARBUCKS TRIPLESHOT ENERGY", None),
    ("Doubleshot energy coffee mocha", "Starbucks", None),
    ("Java Monster Coffee + Energy", "Monster", None),
    ("Coffee + energy drink, mean bean", None, None),
    ("Starbucks Coffee Doubleshot", "Starbucks Can", None),
    ("frappuccino chilled coffee drink", "Starbucks", None),
    ("Pure black cold pressed coffee", "La Colombe", None),
    ("Vanilla Latte", "La Colombe Coffee Workshop", None),
    ("Caramel Macchiato coffee", None, None),
    ("Espresso with Cream", "Black Rifle Coffee Company", None),
    ("Coffee Milk", "Moody", None),
    # Coffee: instant, singles, functional mixes.
    ("NESCAFÉ GOLD BLEND Rich Body Soluble Coffee", "NESCAFÉ", None),
    ("Gold Espresso Intense Coffee", "NESCAFÉ", None),
    ("Goldblend Stick Coffee", "Nescafe", None),
    ("coffee singles teabags", "folgers", None),
    ("Protein Coffee Original", "javvy", None),
    ("coffee + creatine", "Bullet Proof", None),
    ("Mushroom Coffee+ Dark Roast Coffee", "RYZE", None),
    ("Medium Roast Coffee+", "Everyday Dose", None),
    ("Mud Wtr Mushroom Coffee", "Mud Wtr", None),
    ("Focus Organic Coffee", "Four Sigmatic", None),
    # Coffee: creamers, syrups, desserts, candy, substitutes.
    ("The Original Coffee mate Rich & Smooth", "Nestlé", None),
    ("Coffee Enhancer French Vanilla", "International Delight", None),
    ("Vanilla Coffee Syrup", "Signature Select", None),
    ("Coffee Ice Cream", "Häagen-Dazs", None),
    ("Coffee Chip Gelato", "Gelato Boy", None),
    ("Yasso Coffee Frozen Yogurt", "Yasso", None),
    ("Coffee Affogato", "Van Leeuwen", None),
    ("Groundwork Coffee & Toffee", "Alec's", None),
    ("Original caramel coffee hard candies", "Storck", None),
    ("Coffee Dark Chocolate Mexicano", "Taza Chocolate", None),
    ("Sauce hot red habanero black coffee", None, None),
    ("Chicory herbal 'coffee', medium roast", "Teeccino Inc.", None),
    ("Coffee alt hazelnut organic", "Teeccino Inc.", None),
    # Eggs: shell eggs match.
    ("sprouts pasture raised eggs 6ct", "Sprouts", EGGS),
    ("Organic Free Range Large Brown Eggs", "Lidl", EGGS),
    ("EGGS", "EGG•LAND'S BEST", EGGS),
    ("Pasture Vital Farms Pasture Raised Extra Large Eggs", "Vital Farms", EGGS),
    # Eggs: liquid, plant-based and prepared.
    ("liquid whole eggs", None, None),
    ("Eggs from plants", "JUST", None),
    ("Folded plant eggs", "Just Egg", None),
    ("Chorizo & Egg Protein Scramble", "Wunder Eggs", None),
    # Grains and bakery: members.
    ("100% Whole Wheat Bread", "Nature's Own", BREAD),
    ("Simply Sourdough Bread", "Base Culture", BREAD),
    ("Organic Brown Basmati Rice", "Seeds of Change", RICE),
    ("Short Grain Brown Rice", "Lundberg Family Farms", RICE),
    ("Penne Rigate Pasta", "Bowl & Basket", PASTA),
    ("Gluten-free egg noodles", "Taste republic", PASTA),
    ("Bread Flour", "King Arthur Baking", FLOUR),
    ("Organic Brown Rice Flour", "Arrowhead Mills", FLOUR),
    # Grains and bakery: look-alikes.
    ("Organic Mini Bagels Plain Awesome", "Dave's Killer Bread", None),
    ("Brazilian Cheese Bread Cheddar & Parmesan", "Brazi Bites", None),
    ("Garlic bread", "Pepperidge farm", None),
    ("100% Organic Honey", "Rice's Honey", None),
    ("Rice Vinegar", "Marukan", None),
    ("Chicken Fried Rice", "P.F. Chang's Home Menu", None),
    ("Rice Seasoning", "NORI KOMI FURIKAKE", None),
    ("Rao's bolognese pasta sauce", "Rao's Homestyle", None),
    ("Pesto Chicken Pasta", "Kevin's", None),
    ("Pasta Roni Fettuccine Alfredo", "Pasta Roni", None),
    ("White Cheddar Pasta Shells As Packaged", "Kraft", None),
    ("Spaghetti & Meatballs", "Banquet", None),
    ("FLOUR TORTILLAS", "PUEBLO LINDO", None),
    ("PAM Baking with Flour", "PAM", None),
    # Pantry staples: members.
    ("Extra Crunchy Super Chunk Peanut Butter", "SKIPPY", PB),
    ("Peanut Butter & Co Smooth Operator", "Peanut Butter & Co", PB),
    ("Peanut butter", "Jiffy", PB),
    ("Peanut Butter Spread", "Skippy", PB),
    ("Extra Virgin Olive Oil", "Pompeian", OIL),
    ("Extra Virgin Olive Oil Global Blend", "California Olive Ranch", OIL),
    ("Mazola canola oil", "Mazola", OIL),
    ("Avocado oil", None, OIL),
    ("Brown Sugar", "Kinder's", SUGAR),
    ("White Sugar Cubes", "India Tree", SUGAR),
    ("Demerara Sugar", None, SUGAR),
    # Pantry staples: look-alikes.
    ("Peanut butter", None, PB),
    ("Reese's 2 Peanut Butter Cups", None, None),
    ("PB2 Original Powdered Peanut Butter", None, None),
    ("Peanut butter chip", "Protein pint", None),
    ("Peanut butter \u2019s", "M&M", None),
    ("Cosmic Bliss Chocolate peanut butter", None, None),
    ("choc peanut butter protein shake", None, None),
    ("PEANUT BUTTER FILLED PRETZEL NUGGETS", None, None),
    ("Thai Peanut Sauce", None, None),
    ("Butter with Olive Oil & Sea Salt", None, None),
    ("Albacore Tuna in Olive Oil", None, None),
    ("Olive oil cooking spray", None, None),
    ("Mayo Real Mayonnaise With Avocado Oil", None, None),
    ("GF Focaccia Olive Oil And Rosemary", None, None),
    ("Avocado Oil Shortening", None, None),
    ("Organic Olive Oil, Basil & Garlic Sauce", "Bertolli", None),
    ("Sesame Oil", None, None),
    ("Red Wine Vinegar", "Pompeian", None),
    ("Sprinkles white sugar pearls", None, None),
    ("Sweet Brown Sugar Barbecue Sauce", None, None),
    ("Maple And Brown Sugar Instant Oatmeal", "Premier protein", None),
    ("Zero Sugar Cranberry Juice", None, None),
    ("Chocolate High Protein Shake", "Premier Protein", None),
    ("Builders Protein Bar", None, None),
    # Beverages: members.
    ("Purified Drinking Water", "Refreshe, Safeway  Inc., Signature Select", WATER),
    ("VOSS Artesian Water", "CONAXESS TRADE NORGE AS, Voss Production AS", WATER),
    ("Saratoga, Natural Spring Water", "Saratoga Springs Mineral Water", WATER),
    ("Pink Grapefruit Sparkling Water", "Ardor Energy", SPARKLING),
    ("Collagen Sparkling Water, Strawberry", "Vital Proteins", SPARKLING),
    ("Organic 100% orange juice", "365 Everyday Value, Whole Foods Market  Inc.", OJ),
    ("100% Premium Orange Juice from Concentrate", "Florida's Natural", OJ),
    ("Cold Pressed Orange Juice", "Evolution Fresh", OJ),
    ("Coca-Cola Zero Sugar Zero Calorie Cola, 67.6 fl oz", "Coca-Cola, Coke", SODA),
    ("Diet Coke Soda, 8 Fl Oz, 6 Count", "Diet coke", SODA),
    ("Cherry Vanilla Soda", "Olipop", SODA),
    ("Classic Cola Prebiotic Soda", "Poppi", SODA),
    ("Lemon-Lime Soda", "Sprite", SODA),
    ("ORANGE SODA", "Fanta", SODA),
    # Beverages: look-alikes.
    ("Watermelon Mint Energy Seltzer", "Hi-Ball", None),
    ("wild berry sparkling energy seltzer", None, None),
    ("Hi ball energy, sparkling energy water, vanilla", "hi-ball ENERGY", None),
    ("Sparkling Protein Water", "Lumen", None),
    ("Watermelon Infused Water", "Hint", None),
    ("Propel Fitness Water", "Propel", None),
    ("Premium Coconut Water + Pineapple", "Once Upon a Coconut", None),
    ("Cactus Water Watermelon", "Cali Water", None),
    ("Pure Baking Soda", "Meijer", None),
    ("Soda de mel\u00f3n con helado de vainilla", "felice", None),
    ("Orange Blossom Water", "Carlo", None),
    ("Tuna fillet in water", "Tonnino", None),
    ("Sliced Water Chestnuts", "Dynasty", None),
    ("Sparkling Apple Juice", None, None),
    ("Apple juice", "Minute Maid", None),
    ("Juice Cocktail From Concentrate", None, None),
    ("Lakewood Organic Orange Mango Juice", "Lakewood", None),
    ("Splash flavored juice beverage", None, None),
    ("Pineapple Chunks In 100% Pineapple Juice", "Dole", None),
    ("Mandarin Oranges in 100% fruit juice", "Dole", None),
    ("Voodoo Ranger Juice Force IPA Hazy Imperial IPA", "New Belgium", None),
    ("Sparkling Orange Flavored Espresso Tonic Beverage", "Trader Joe's", None),
    # Household bags: tea bags, snack bags and brands with "liner" stay out.
    ("Earl grey black tea bags", "twinings", None),
    ("Organic Awake english breakfast tea 16 tea bags", "Tazo", None),
    ("Nerds gummy clusters peg bag", "Nerds", None),
    ("Sabritas Chili Lime Peanuts 7 Ounce Plastic Bag", None, None),
    ("Asian Grill Rubbed Salmon", "Sea Cuisine, High Liner Foods (Usa) Incorporated", None),
    # Household paper and bags: precedence and collisions (no real members in the data).
    ("Kleenex Facial Tissues 2-Ply", "Kleenex", "household/facial-tissue"),
    ("Bath Tissue Ultra Soft 2-Ply", "Charmin", "household/toilet-paper"),
    ("Tissue Paper Gift Wrap White", None, None),
    ("Facial Tissue Box Cover", None, None),
    ("Glad Tall Kitchen Trash Bags 13 Gallon", "Glad", "household/trash-bags"),
    ("Zip Top Storage Bags Gallon", "Ziploc", None),
    ("Compost Bags Compostable 3 Gallon", "BioBag", None),
    ("Dog Waste Poop Bags", None, None),
    # Cleaning and laundry: no real members in the data; precedence and collisions only.
    ("Free clear mighty pacs", "all", None),
    ("Tide Original Liquid Laundry Detergent", "Tide", "household/laundry-detergent"),
    ("Tide Fabric Softener", "Tide", None),
    ("Downy Fabric Softener Dryer Sheets", "Downy", None),
    ("Shout Laundry Stain Remover Spray", "Shout", None),
    ("Dawn Ultra Dish Soap", "Dawn", "household/dish-soap"),
    ("Dawn Hand Renewal Hand Soap", "Dawn", None),
    ("Dove Body Wash", "Dove", None),
    ("Cascade Dishwasher Detergent Pods 40 Count", "Cascade", "household/dishwasher-detergent"),
    ("Finish Dishwasher Cleaner", "Finish", None),
    ("Dishwasher Detergent Dish Soap", "Dawn", "household/dishwasher-detergent"),
]



def _offer(title, brand):
    return RawOffer(
        retailer="openprices",
        retailer_item_id="1",
        title=title,
        price=Decimal("1.00"),
        currency="USD",
        url="https://example.invalid",
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        brand=brand,
    )


@pytest.mark.parametrize(("title", "brand", "expected"), CASES, ids=[c[0] for c in CASES])
def test_observed_title_category(title, brand, expected):
    assert match_category(_offer(title, brand), CATEGORIES) == expected
