"""The active shopping list on Radicale: the dicts the planner sees, and what lands on the list."""
import unittest
from html import escape

from mealie_planner import radicale_list
from mealie_planner.radicale_list import RadicaleList, summary

URL = "https://dav.example/nathan/shopping/"
LABELS = [{"id": "lab-produce", "name": "Produce"}, {"id": "lab-dairy", "name": "Dairy"},
          {"id": "lab-baking", "name": "6. Baking, Spices"}]


class FakeDav:
    def __init__(self):
        self.store = {}

    def __call__(self, method, url, body=None, headers=None):
        if method == "REPORT":
            parts = "".join(f"<d:response><d:href>{h}</d:href><d:propstat><d:prop><d:getetag>\"1\"</d:getetag>"
                            f"<c:calendar-data>{escape(v)}</c:calendar-data></d:prop></d:propstat></d:response>"
                            for h, v in self.store.items())
            return 207, f'<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">{parts}</d:multistatus>'
        if method == "GET":
            return (200, self.store[url]) if url in self.store else (404, "")
        if method == "PUT":
            if (headers or {}).get("If-None-Match") == "*" and url in self.store:
                return 412, ""
            self.store[url] = body
            return 201, ""
        if method == "DELETE":
            return (204, "") if self.store.pop(url, None) is not None else (404, "")
        raise AssertionError(method)


def make():
    lst = RadicaleList(URL, "u", "p", lambda: LABELS)
    fake = FakeDav()
    lst._http = fake
    return lst, fake


class TestSummary(unittest.TestCase):
    def test_quantity_goes_in_front_once(self):
        self.assertEqual(summary("lbs chicken thighs", 2), "2 lbs chicken thighs")
        self.assertEqual(summary("1 lb beef", 1), "1 lb beef")          # already numbered
        self.assertEqual(summary("lb beef", 1), "1 lb beef")            # a unit follows
        self.assertEqual(summary("bananas", 1), "bananas")              # bare 1 dropped
        self.assertEqual(summary("eggs", 12), "12 eggs")
        self.assertEqual(summary("milk", 0), "milk")
        self.assertEqual(summary("cream", 0.5), "0.5 cream")


class TestRadicaleList(unittest.TestCase):
    def test_add_read_update_delete_round_trip(self):
        lst, fake = make()
        [synced] = lst.add([{"note": "lbs chicken", "quantity": 2, "labelId": "lab-produce",
                             "extras": {"is_synced": True}, "checked": False}])
        [manual] = lst.add([{"note": "Coffee, dark; whole bean", "quantity": 0}])
        self.assertTrue(synced["id"].startswith("dav-"))
        items = {i["id"]: i for i in lst.items()}
        self.assertEqual(items[synced["id"]]["note"], "2 lbs chicken")
        self.assertEqual(items[synced["id"]]["quantity"], 0)
        self.assertEqual(items[synced["id"]]["label"], {"name": "Produce"})
        self.assertEqual(items[synced["id"]]["labelId"], "lab-produce")
        self.assertEqual(items[synced["id"]]["extras"], {"is_synced": True})
        self.assertEqual(items[manual["id"]]["note"], "Coffee, dark; whole bean")   # escaping survives
        self.assertEqual(items[manual["id"]]["extras"], {})

        lst.update([{**items[manual["id"]], "checked": True}])
        self.assertTrue({i["id"]: i for i in lst.items()}[manual["id"]]["checked"])
        ics = fake.store[URL + manual["id"][4:] + ".ics"]
        self.assertIn("@mealie-planner-manual", ics)   # who made it never changes on update

        self.assertEqual(lst.clear_synced(), 1)        # a wipe keeps what people added
        self.assertEqual([i["id"] for i in lst.items()], [manual["id"]])
        lst.delete([manual["id"]])
        self.assertEqual(lst.items(), [])

    def test_items_made_elsewhere_are_manual_and_keep_their_aisle(self):
        lst, fake = make()
        fake.store[URL + "abc.ics"] = ("BEGIN:VCALENDAR\r\nBEGIN:VTODO\r\nUID:abc@assistant\r\nSUMMARY:Milk\r\n"
                                       "CATEGORIES:Dairy\r\nSTATUS:NEEDS-ACTION\r\nBEGIN:VALARM\r\n"
                                       "DESCRIPTION:Reminder\r\nEND:VALARM\r\nEND:VTODO\r\nEND:VCALENDAR\r\n")
        [milk] = lst.items()
        self.assertEqual((milk["id"], milk["note"], milk["labelId"], milk["extras"]), ("dav-abc", "Milk", "lab-dairy", {}))
        lst.update([{**milk, "labelId": None, "checked": True}])
        self.assertIn("CATEGORIES:Dairy", fake.store[URL + "abc.ics"])
        self.assertIn("UID:abc@assistant", fake.store[URL + "abc.ics"])

    def test_an_aisle_with_a_comma_survives(self):
        lst, fake = make()
        lst.add([{"note": "cumin", "labelId": "lab-baking"}])
        [cumin] = lst.items()
        self.assertEqual((cumin["label"], cumin["labelId"]), ({"name": "6. Baking, Spices"}, "lab-baking"))

    def test_bad_ids_are_refused(self):
        lst, _ = make()
        with self.assertRaises(ValueError):
            lst.delete(["dav-../../todo/x"])
        self.assertFalse(radicale_list.is_dav_id("9a1e2d1e33f24f27a01fef55c89a92de"))


if __name__ == "__main__":
    unittest.main()
