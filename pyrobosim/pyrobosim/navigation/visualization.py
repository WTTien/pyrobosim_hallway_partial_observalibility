"""Visualization utilities for path planners."""

from typing import Sequence

import matplotlib.pyplot as plt
from matplotlib.artist import Artist
from matplotlib.axes import Axes
from matplotlib.collections import LineCollection

from ..utils.path import Path
from ..utils.search_graph import SearchGraph
from ..utils.pose import Pose


def plot_path_planner(
    axes: Axes,
    graphs: list[SearchGraph] = [],
    path: Path | None = None,
    path_color: Sequence[float] | str = "m",
) -> dict[str, list[Artist]]:
    """
    Plots the planned path on a specified set of axes.

    :param axes: The axes on which to draw.
    :param graphs: A list of path planner graphs to display.
    :param path: Path to display.
    :param path_color: Color of the path, as an RGB tuple or string.
    :return: List of Matplotlib artists containing what was drawn,
        used for bookkeeping.
    """
    graph_artists: list[Artist] = []
    path_artists: list[Artist] = []
    artists: dict[str, list[Artist]] = {}

    for graph in graphs:
        # Plot the markers
        (markers,) = axes.plot(
            [n.pose.x for n in graph.nodes],
            [n.pose.y for n in graph.nodes],
            color=graph.color,
            alpha=graph.color_alpha,
            linestyle="",
            marker="o",
            markerfacecolor=graph.color,
            markeredgecolor=graph.color,
            markersize=3,
            zorder=1,
        )
        graph_artists.append(markers)

        # Plot the edges as a LineCollection
        edge_coords = [
            [[e.nodeA.pose.x, e.nodeA.pose.y], [e.nodeB.pose.x, e.nodeB.pose.y]]
            for e in graph.edges
        ]
        line_segments = LineCollection(
            edge_coords,
            color=graph.color,
            alpha=graph.color_alpha,
            linewidth=0.5,
            linestyle="--",
            zorder=1,
        )
        axes.add_collection(line_segments)
        graph_artists.append(line_segments)

    if path and path.num_poses > 0:
        x = [p.x for p in path.poses]
        y = [p.y for p in path.poses]
        (path,) = axes.plot(
            x, y, linestyle="-", color=path_color, linewidth=3, alpha=0.5, zorder=1
        )
        (start,) = axes.plot(x[0], y[0], "go", zorder=2)
        (goal,) = axes.plot(x[-1], y[-1], "rx", zorder=2)
        path_artists.extend((path, start, goal))

    if graph_artists:
        artists["graph"] = graph_artists
    if path_artists:
        artists["path"] = path_artists
    return artists


def show_path_planner(
    graphs: list[SearchGraph] = [],
    path: Path | None = None,
    title: str = "Path Planner Output",
) -> None:
    """
    Shows the path planner output in a new figure.

    :param graphs: A list of path planner graphs to display.
    :param path: Path to display.
    :param title: Title to display.
    """

    f = plt.figure()
    ax = f.add_subplot(111)
    plot_path_planner(ax, graphs=graphs, path=path)
    plt.title(title)
    plt.axis("equal")
    plt.show()

def plot_scan_poses(
        axes: Axes,
        poses: list[Pose] = [],
) -> dict[str, list[Artist]]:
    """"
    Plots the scan poses on a specified set of axes.

    :param axes: The axes on which to draw.
    :param poses: The poses to display.
    :return: List of Matplotlib artists containing what was drawn,
        used for bookkeeping.
    """
    poses_artists: list[Artist] = []
    artists: dict[str, list[Artist]] = {}

    (markers,) = axes.plot(
        [pose.x for pose in poses],
        [pose.y for pose in poses],
        color = 'y',
        marker='1',
        linestyle='',
        markerfacecolor='y',
        markeredgecolor='y',
        markersize=3,
        zorder=1,
    )
    poses_artists.append(markers)

    if poses_artists:
        artists["poses"] = poses_artists
    return artists
    
