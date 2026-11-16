# 1. Theory: if Bob deleted a fraction f of the chunks, one random check finds a
#    missing chunk with chance f. He survives one check with chance (1 - f), so he
#    survives all 4 checks x 3 periods with chance (1 - f)^12.
#       chance caught = 1 - (1 - f)^(checks per period x periods)
# 2. Experiment: run real deals on the local chain with the real contract, where Bob
#    deletes a fraction f of the file, and count how often he is caught.
# 3. Graph: theory lines + measured dots, saved as detection_graph.png.
import contextlib
import io
import os
import tempfile

import matplotlib
matplotlib.use("Agg")  # draw to a file, no window
import matplotlib.pyplot as plt

import chain
import provider
import user

fractions = [0.02, 0.05, 0.1, 0.2, 0.5]  # how much of the file Bob deletes
trials = 40                              # deals per fraction


# Theory: chance Bob is caught at some point during the deal.
def chance_caught(f, checks, periods):
    return 1 - (1 - f) ** (checks * periods)


# Runs real cheating deals and returns how many times Bob was caught.
def experiment(w3, market, data, folder, f):
    caught = 0
    real_f = 0
    for t in range(trials):
        with contextlib.redirect_stdout(io.StringIO()):  # hide the demo prints
            deal_id = user.create_deal(w3, market, data, folder)
            periods_proven = provider.run(w3, market, deal_id, folder, cheat_fraction=f)
        if periods_proven < user.num_periods:
            caught += 1
        # Bob deletes a whole number of chunks, so the real fraction is a bit smaller
        parts = chain.get_deal(market, deal_id)["numChunks"]
        real_f = int(f * parts) / parts
    return caught, real_f


def main():
    checks = user.challenges
    periods = user.num_periods
    data = os.urandom(1024 * 1024)  # 1 MB file
    folder = tempfile.mkdtemp()

    # 1 + 2: theory next to the real experiment
    anvil, w3 = chain.start_anvil(8549)
    try:
        market = chain.deploy(w3)
        results = []
        for f in fractions:
            print(f"running {trials} deals where Bob deletes {f:.0%} ...")
            caught, real_f = experiment(w3, market, data, folder, f)
            results.append((f, real_f, caught))
    finally:
        anvil.terminate()
        anvil.wait()

    print(f"\n{checks} checks per period, {periods} periods, {trials} deals per row:")
    print("| Bob deletes | theory: caught | experiment: caught |")
    print("|---|---|---|")
    for f, real_f, caught in results:
        theory = chance_caught(real_f, checks, periods)
        print(f"| {f:.0%} | {theory:.1%} | {caught}/{trials} = {caught / trials:.1%} |")

    # When is Bob (almost) always caught? Smallest f with chance >= 99%.
    f = 0.0
    while chance_caught(f, checks, periods) < 0.99:
        f += 0.001
    print(f"\nBob is caught with 99% chance once he deletes {f:.1%} of the file or more.")
    print("If he deletes everything, the very first check catches him (100%).")

    # 3: the graph
    xs = [i / 100 for i in range(0, 51)]
    plt.figure(figsize=(7, 4.5))
    lines = [(1, "#eb6834"), (checks, "#2a78d6"), (8, "#1baf7a")]
    for k, color in lines:
        ys = [chance_caught(x, k, periods) for x in xs]
        width = 2.5 if k == checks else 1.5
        name = f"{k} checks per period" + (" (my choice)" if k == checks else "")
        plt.plot(xs, ys, color=color, linewidth=width, label=name)

    measured_x = [real_f for f, real_f, caught in results]
    measured_y = [caught / trials for f, real_f, caught in results]
    plt.scatter(measured_x, measured_y, s=60, color="#2a78d6", edgecolor="white", linewidth=2,
                zorder=3, label=f"experiment ({trials} real deals per dot)")

    plt.xlabel("fraction of the file Bob deleted")
    plt.ylabel("chance Bob is caught during the deal")
    plt.title(f"Catching a cheating provider ({periods} periods)")
    plt.xlim(0, 0.5)
    plt.ylim(0, 1.02)
    plt.gca().xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    plt.gca().yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    plt.grid(alpha=0.25)
    plt.legend(loc="lower right", frameon=False)
    plt.tight_layout()
    plt.savefig("detection_graph.png", dpi=150)
    print("saved detection_graph.png")


if __name__ == "__main__":
    main()
