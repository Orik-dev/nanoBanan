# CREDITS_PER_GENERATION = 1

# PACKS_RUB = [149, 240, 540]

# PACKS_CREDITS: dict[int, int] = {
#     149: 50,
#     240: 100,
#     540: 300,
# }

# def credits_for_rub(rub: int) -> int:
#     return PACKS_CREDITS.get(rub, 0)


CREDITS_PER_GENERATION = 1

PACKS_RUB = [149, 290, 690, 990]

PACKS_CREDITS: dict[int, int] = {
    149: 50,
    290: 100,
    690: 300,
    990: 500,
}

def credits_for_rub(rub: int) -> int:
    return PACKS_CREDITS.get(rub, 0)